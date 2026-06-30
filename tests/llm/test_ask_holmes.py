# type: ignore
import os
import shutil
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime
from os import path
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest
from holmes.config import Config
from holmes.core.conversations import build_chat_messages
from holmes.core.models import ChatRequest
from holmes.core.prompt import PromptComponent, build_initial_ask_messages
from holmes.core.tool_calling_llm import LLMResult, ToolCallingLLM
from holmes.core.tools_utils.filesystem_result_storage import tool_result_storage
from holmes.core.tools_utils.frontend_tools import inject_frontend_tools
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.tracing import SpanType, TracingFactory
from holmes.plugins.skills.skill_loader import SkillCatalog, load_skill_catalog
from tests.llm.utils.braintrust import log_to_braintrust
from tests.llm.utils.classifiers import evaluate_correctness
from tests.llm.utils.commands import apply_env_config, set_test_env_vars
from tests.llm.utils.denied_commands import extract_denied_commands
from tests.llm.utils.env_config import EnvConfig, get_env_configs
from tests.llm.utils.iteration_utils import get_test_cases
from tests.llm.utils.mock_dal import load_test_dal
from tests.llm.utils.test_toolset import TestToolsetManager
from tests.llm.utils.property_manager import (
    handle_test_error,
    set_initial_properties,
    set_trace_properties,
    update_property,
    update_test_results,
)
from tests.llm.utils.skill_suggestions import (
    count_fetch_skill_calls,
    extract_suggested_skills,
    write_suggestions_as_skill_files,
)
from tests.llm.utils.retry_handler import retry_on_throttle
from tests.llm.utils.test_case_utils import (
    AskHolmesTestCase,
    Evaluation,
    check_and_skip_test,
    create_eval_llm,
    get_models,
    load_frontend_tools,
)

TEST_CASES_FOLDER = Path(
    path.abspath(path.join(path.dirname(__file__), "fixtures", "test_ask_holmes"))
)


def get_ask_holmes_test_cases():
    return get_test_cases(TEST_CASES_FOLDER)


def _get_env_config_ids():
    """Generate ids for env_config parameterization."""
    return [ec.name for ec in get_env_configs()]


@pytest.mark.llm
@pytest.mark.parametrize("env_config", get_env_configs(), ids=_get_env_config_ids())
@pytest.mark.parametrize("model", get_models())
@pytest.mark.parametrize("test_case", get_ask_holmes_test_cases())
def test_ask_holmes(
    env_config: EnvConfig,
    model: str,
    test_case: AskHolmesTestCase,
    caplog,
    request,
    additional_system_prompt,
    shared_test_infrastructure,  # type: ignore
):
    # Set initial properties early so they're available even if test fails
    set_initial_properties(request, test_case, model, env_config)

    tracer = TracingFactory.create_tracer("braintrust")
    metadata = {"model": model, "env_config": env_config.name}
    tracer.start_experiment(additional_metadata=metadata)

    result: Optional[LLMResult] = None

    try:
        with tracer.start_trace(
            name=f"{test_case.id}[{model}][{env_config.name}]", span_type=SpanType.EVAL
        ) as eval_span:
            set_trace_properties(request, eval_span)
            check_and_skip_test(test_case, request, shared_test_infrastructure)

            with ExitStack() as stack:
                stack.enter_context(apply_env_config(env_config))

                if test_case.mocked_date:
                    mocked_datetime = datetime.fromisoformat(
                        test_case.mocked_date.replace("Z", "+00:00")
                    )
                    mock_datetime = stack.enter_context(
                        patch("holmes.plugins.prompts.datetime")
                    )
                    mock_datetime.now.return_value = mocked_datetime
                    mock_datetime.side_effect = None
                    mock_datetime.configure_mock(
                        **{"now.return_value": mocked_datetime, "side_effect": None}
                    )

                stack.enter_context(set_test_env_vars(test_case))

                retry_enabled = request.config.getoption(
                    "retry-on-throttle", default=True
                )
                # Externally-authored skills the test wants pre-loaded into
                # the SkillsToolset before the primary pass (e.g. a
                # deliberately-misleading skill to test resilience).
                preloaded = getattr(test_case, "pre_loaded_skills_path", None)
                preloaded_paths = (
                    [os.path.join(test_case.folder, preloaded)] if preloaded else None
                )
                result = retry_on_throttle(
                    ask_holmes,
                    test_case,  # positional arg
                    model,  # positional arg
                    tracer,  # positional arg
                    eval_span,  # positional arg
                    additional_system_prompt=additional_system_prompt,
                    additional_skill_paths=preloaded_paths,
                    request=request,
                    retry_enabled=retry_enabled,
                    test_id=test_case.id,
                    model=model,  # Also pass for logging in retry_handler
                )

    except Exception as e:
        handle_test_error(
            request=request,
            error=e,
            eval_span=eval_span if "eval_span" in locals() else None,
            test_case=test_case,
            model=model,
            result=result,
        )
        raise

    output = result.result

    suggested_memories = extract_suggested_skills(result.tool_calls)
    update_property(request, "suggested_memories", suggested_memories)
    update_property(request, "memories_count", len(suggested_memories))
    update_property(
        request, "skills_read_count", count_fetch_skill_calls(result.tool_calls)
    )

    scores = update_test_results(
        request=request,
        output=output,
        tools_called=[tc.description for tc in result.tool_calls]
        if result.tool_calls
        else [],
        scores=None,  # Let it calculate
        result=result,
        test_case=test_case,
        eval_span=eval_span,
        caplog=caplog,
        suggested_memories=suggested_memories
        if test_case.memories_generated is not None
        else None,
    )

    # Hard yes/no skill-suggestion count check. Content quality is scored by
    # the LLM judge via update_test_results above (the judge sees the emitted
    # suggestions and the eval's expected_output together). The correctness
    # score is reset to 0 BEFORE the Braintrust logging below and before the
    # assertions fire, so both Braintrust and the GitHub markdown report
    # reflect the failure even though the judge already wrote a 1.
    memory_check_failed = False
    if test_case.memories_generated is not None:
        actual_memories = len(suggested_memories)
        memory_check_failed = (
            test_case.memories_generated and actual_memories < 1
        ) or (not test_case.memories_generated and actual_memories != 0)
        if memory_check_failed:
            update_property(request, "actual_correctness_score", 0)
            scores["correctness"] = 0

    # Deterministic skill-UPDATE check: every skill named in
    # expected_skill_updates must be referenced by some suggestion's
    # `updates_skill` field — i.e. the agent recognized that a loaded skill
    # was wrong and proposed a correction to it, not a parallel duplicate.
    missing_skill_updates: List[str] = []
    if test_case.expected_skill_updates:
        actual_updates = {
            str(s.get("updates_skill") or "").strip()
            for s in suggested_memories
        }
        missing_skill_updates = [
            name
            for name in test_case.expected_skill_updates
            if name not in actual_updates
        ]
        if missing_skill_updates:
            update_property(request, "actual_correctness_score", 0)
            scores["correctness"] = 0

    if eval_span:
        log_to_braintrust(
            eval_span=eval_span,
            test_case=test_case,
            model=model,
            result=result,
            scores=scores,
            suggested_memories=suggested_memories,
        )

    if memory_check_failed:
        if test_case.memories_generated:
            raise AssertionError(
                f"Test {test_case.id} expected at least one skill suggestion "
                f"but the LLM emitted zero. The eval is designed to teach an "
                f"env-specific lesson; if Holmes isn't capturing it the "
                f"SuggestSkills prompt/tool needs tightening."
            )
        raise AssertionError(
            f"Test {test_case.id} expected NO skill suggestions but the "
            f"LLM emitted {len(suggested_memories)}. This usually means the "
            f"SuggestSkills tool/prompt is being too eager. "
            f"Suggestions:\n{suggested_memories}"
        )

    if missing_skill_updates:
        raise AssertionError(
            f"Test {test_case.id} expected suggestion(s) correcting the "
            f"loaded skill(s) {missing_skill_updates} (via the "
            f"`updates_skill` field), but no emitted suggestion referenced "
            f"them. The agent either proposed a duplicate skill instead of "
            f"an update, or failed to flag the bad skill at all. "
            f"Suggestions:\n{suggested_memories}"
        )

    # Get expected for assertion message
    expected_output = test_case.expected_output
    if isinstance(expected_output, list):
        expected_output = "\n-  ".join(expected_output)

    assert (
        int(scores.get("correctness", 0)) == 1
    ), f"Test {test_case.id} failed (score: {scores.get('correctness', 0)})\nActual: {output}\nExpected: {expected_output}"

    # Check token limit if configured
    if test_case.max_tokens is not None:
        actual_tokens = result.total_tokens
        assert actual_tokens <= test_case.max_tokens, (
            f"Test {test_case.id} exceeded token limit: "
            f"used {actual_tokens} tokens, max allowed is {test_case.max_tokens}"
        )

    # The primary pass succeeded — all primary assertions above passed. We
    # flag it explicitly so the report can show the primary row as ✅ even
    # if the replay block below fails the test (pytest's overall status
    # would otherwise paint both rows red). The replay row's own status
    # comes from replay_correctness + replay_skill_loaded.
    update_property(request, "primary_passed", True)

    # Closed-loop replay: write the suggestions the first pass emitted as
    # SKILL.md files in a tempdir, run the same prompt again with those
    # skills injected, and check that the agent (a) fetched the skill —
    # proving it judged the suggestion relevant — and (b) still produces
    # the correct answer. Skips when no suggestions were emitted or
    # rerun_with_memory is not set.
    replay_eligible = (
        test_case.memories_generated
        and getattr(test_case, "rerun_with_memory", False)
        and suggested_memories
    )
    if replay_eligible:
        update_property(request, "replay_attempted", True)
        with tempfile.TemporaryDirectory(
            prefix=f"replay-{test_case.id}-"
        ) as skills_dir:
            # If the test pre-loaded existing skills (e.g. simulating a
            # customer who already saved a skill from a previous
            # investigation), copy them into the replay tempdir so they
            # remain visible to the replay agent alongside the newly
            # captured ones.
            preloaded = getattr(test_case, "pre_loaded_skills_path", None)
            if preloaded:
                preloaded_abs = os.path.join(test_case.folder, preloaded)
                if os.path.isdir(preloaded_abs):
                    for entry in os.listdir(preloaded_abs):
                        src = os.path.join(preloaded_abs, entry)
                        dst = os.path.join(skills_dir, entry)
                        if os.path.isdir(src):
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)

            written = write_suggestions_as_skill_files(
                suggested_memories, skills_dir
            )

            # Optional assertion on the number of skill files written from
            # the captured suggestions (e.g. multi-quirk evals can pin how
            # many separate skills the agent should have proposed).
            expected_count = getattr(test_case, "expected_skill_count", None)
            if expected_count is not None:
                assert len(written) == expected_count, (
                    f"Test {test_case.id} expected {expected_count} "
                    f"skill file(s) from {len(suggested_memories)} captured "
                    f"suggestion(s), but got {len(written)}."
                )
            try:
                with tracer.start_trace(
                    name=f"{test_case.id}[replay][{model}]",
                    span_type=SpanType.EVAL,
                ) as replay_span:
                    replay_start = time.time()
                    try:
                        replay_result = ask_holmes(
                            test_case=test_case,
                            model=model,
                            tracer=tracer,
                            eval_span=replay_span,
                            additional_system_prompt=additional_system_prompt,
                            # Replay simulates a FUTURE investigation: the
                            # captured skills are available, but the
                            # SuggestSkills tool (and its prompt snippet) are
                            # not re-injected. It asks the EXACT same question
                            # as the primary run, so the primary-vs-replay
                            # metrics in the report are a clean with-skill vs
                            # without-skill comparison on identical input.
                            inject_frontend=False,
                            additional_skill_paths=[skills_dir],
                            # Do NOT pass `request` here: ask_holmes appends
                            # holmes_duration / num_llm_calls / tool_call_count
                            # to user_properties when given a request, and the
                            # report reads the LAST value per key — so the
                            # replay's numbers would overwrite the PRIMARY
                            # run's Time/Turns/Tools columns in the report.
                            # Replay metrics are recorded separately below
                            # under replay_* keys.
                            request=None,
                        )
                    except Exception as e:
                        # The rerun itself crashed — record an error row on
                        # the replay trace so it isn't left empty in
                        # Braintrust.
                        log_to_braintrust(
                            replay_span, test_case, model, result=None, error=e
                        )
                        raise
                    replay_duration = time.time() - replay_start
                    # The replay runs as its own Braintrust trace (named
                    # "<id>[replay][<model>]"); record its span ids so the
                    # report's [replay] row can link to it instead of the
                    # primary trace.
                    if hasattr(replay_span, "id"):
                        update_property(
                            request,
                            "replay_braintrust_span_id",
                            str(replay_span.id),
                        )
                    if hasattr(replay_span, "root_span_id"):
                        update_property(
                            request,
                            "replay_braintrust_root_span_id",
                            str(replay_span.root_span_id),
                        )
                    replay_tool_calls = replay_result.tool_calls or []
                    replay_fetch_skill_count = count_fetch_skill_calls(
                        replay_tool_calls
                    )
                    fetch_skill_called = replay_fetch_skill_count > 0
                    # Capture the full LLMResult stats for the replay so the
                    # GitHub report can show side-by-side duration / tokens /
                    # cost vs the original run.
                    update_property(
                        request, "replay_turns", replay_result.num_llm_calls
                    )
                    update_property(
                        request, "replay_tool_calls_count", len(replay_tool_calls)
                    )
                    update_property(request, "replay_skill_loaded", fetch_skill_called)
                    update_property(
                        request, "replay_skills_read_count", replay_fetch_skill_count
                    )
                    update_property(request, "replay_skill_count", len(written))
                    update_property(request, "replay_duration", replay_duration)
                    for attr in (
                        "total_cost",
                        "total_tokens",
                        "prompt_tokens",
                        "completion_tokens",
                        "cached_tokens",
                        "reasoning_tokens",
                        "max_completion_tokens_per_call",
                        "max_prompt_tokens_per_call",
                        "num_compactions",
                    ):
                        value = getattr(replay_result, attr, None)
                        if value is not None:
                            update_property(request, f"replay_{attr}", value)
                    replay_output = replay_result.result or ""
                    update_property(request, "replay_answer", replay_output)

                    # Score replay correctness with the same judge — but
                    # separately, so the original correctness reading is
                    # preserved.
                    # The replay asks the same question, so it is judged
                    # against the primary's `expected_output` by default.
                    # Fixtures whose expected_output includes
                    # SuggestSkills-specific criteria (which can never hold
                    # on replay — the tool isn't injected there) declare
                    # `expected_replay_output` with the answer-only criteria
                    # instead.
                    expected = (
                        getattr(test_case, "expected_replay_output", None)
                        or test_case.expected_output
                    )
                    if not isinstance(expected, list):
                        expected = [expected]
                    evaluation_type = "strict"
                    if hasattr(test_case, "evaluation") and isinstance(
                        test_case.evaluation.correctness, Evaluation
                    ):
                        evaluation_type = test_case.evaluation.correctness.type
                    replay_eval = evaluate_correctness(
                        output=replay_output,
                        expected_elements=expected,
                        parent_span=replay_span,
                        evaluation_type=evaluation_type,
                        caplog=caplog,
                    )
                    update_property(
                        request, "replay_correctness", int(replay_eval.score)
                    )

                    # Record the replay row (answer, expected, correctness
                    # score, token/cost metadata) on its own Braintrust trace
                    # BEFORE the hard assertions below — a failed replay
                    # otherwise leaves an empty trace with no way to see what
                    # the model actually answered.
                    log_to_braintrust(
                        replay_span,
                        test_case,
                        model,
                        result=replay_result,
                        scores={"correctness": replay_eval.score},
                        expected_override=str(expected),
                    )

                    # Hard assertions: the agent must have fetched the skill
                    # (so we know the captured suggestion was actually
                    # consulted) and the answer must still be correct.
                    require_load = getattr(
                        test_case, "require_skill_load_on_replay", True
                    )
                    assert (not require_load) or fetch_skill_called, (
                        f"Test {test_case.id} replay: the LLM did NOT call fetch_skill, "
                        f"so the captured skill was ignored. Either the skill "
                        f"name/description wasn't relevant enough, or the agent isn't "
                        f"using available skills for this kind of question. Replay tool "
                        f"calls: {[getattr(tc, 'tool_name', '?') for tc in replay_tool_calls]}"
                    )
                    assert int(replay_eval.score) == 1, (
                        f"Test {test_case.id} replay: the answer was wrong even with "
                        f"the skill available. Skill content may be misleading or "
                        f"incomplete.\nActual: {replay_output[:500]}"
                    )

                    # Discovery-style evals: the captured skill encodes facts
                    # (e.g. an index schema) that should make specific
                    # exploration calls unnecessary on replay. If the agent
                    # still made them, the skill content didn't actually
                    # obviate the rediscovery it was saved for.
                    forbidden = (
                        getattr(test_case, "replay_forbidden_tools", None) or []
                    )
                    if forbidden:
                        replay_tool_names = [
                            getattr(tc, "tool_name", "?") for tc in replay_tool_calls
                        ]
                        offending = [t for t in replay_tool_names if t in forbidden]
                        assert not offending, (
                            f"Test {test_case.id} replay: the agent called "
                            f"{offending} even though the captured skill should have "
                            f"made those calls unnecessary. Replay tool calls: "
                            f"{replay_tool_names}"
                        )
            except Exception as e:
                update_property(request, "replay_error", str(e)[:300])
                raise


# TODO: can this call real ask_holmes so more of the logic is captured
def ask_holmes(
    test_case: AskHolmesTestCase,
    model: str,
    tracer,
    eval_span,
    additional_system_prompt,
    request=None,
    additional_skill_paths: Optional[List[str]] = None,
    inject_frontend: bool = True,
) -> LLMResult:
    # The closed-loop replay pass asks the same user_prompt but skips
    # frontend tool injection (no SuggestSkills on replay), while injecting
    # the captured suggestions as skills via additional_skill_paths.
    user_prompt = test_case.user_prompt

    with eval_span.start_span(
        "Initialize Toolsets",
        type=SpanType.TASK.value,
    ) as toolset_span:
        toolset_manager = TestToolsetManager(
            test_case_folder=test_case.folder,
            allow_toolset_failures=getattr(test_case, "allow_toolset_failures", False),
            toolsets_config_path=getattr(test_case, "toolsets_config_path", None),
            additional_skill_paths=additional_skill_paths,
            enable_todo=getattr(test_case, "enable_todo", False),
        )

        tool_executor = ToolExecutor(toolset_manager.toolsets)
        enabled_toolsets = [t.name for t in tool_executor.enabled_toolsets]
        print(
            f"\n🛠️  ENABLED TOOLSETS ({len(enabled_toolsets)}):",
            ", ".join(enabled_toolsets),
        )
        toolset_span.log(metadata={"toolset_names": enabled_toolsets})

    with tool_result_storage() as tool_results_dir:
        ai = ToolCallingLLM(
            tool_executor=tool_executor,
            max_steps=100,
            llm=create_eval_llm(model=model, tracer=tracer),
            tool_results_dir=tool_results_dir,
        )

        # Inject client-defined tools the same way the server does for the
        # `frontend_tools` field of /api/chat requests (e.g. the Robusta UI's
        # skill-suggestion tool). Must happen before building messages so the
        # system prompt reflects the injected tools. The client may also ship
        # a system prompt snippet alongside its tools (mirroring the
        # `additional_system_prompt` request field).
        frontend_payload = load_frontend_tools(test_case) if inject_frontend else None
        if frontend_payload:
            print(
                f"\n🖥️  FRONTEND TOOLS ({len(frontend_payload.tools)}): "
                + ", ".join(t.name for t in frontend_payload.tools)
                + (
                    " (+ additional system prompt)"
                    if frontend_payload.additional_system_prompt
                    else ""
                )
            )
            ai, _ = inject_frontend_tools(ai, frontend_payload.tools)
            if frontend_payload.additional_system_prompt:
                additional_system_prompt = "\n\n".join(
                    p
                    for p in [
                        additional_system_prompt,
                        frontend_payload.additional_system_prompt,
                    ]
                    if p
                )

        # Todos (TodoWrite) are disabled by default in evals; turn off the
        # related prompt instructions/reminder unless the test opts in. The
        # TodoWrite tool itself is dropped by TestToolsetManager (above).
        prompt_component_overrides = None
        if not getattr(test_case, "enable_todo", False):
            prompt_component_overrides = {
                PromptComponent.TODOWRITE_INSTRUCTIONS: False,
                PromptComponent.TODOWRITE_REMINDER: False,
            }

        test_type = (
            test_case.test_type
            or os.environ.get("ASK_HOLMES_TEST_TYPE", "cli").lower()
        )
        if test_type == "cli":
            if test_case.conversation_history:
                pytest.skip("CLI mode does not support conversation history tests")
            else:
                if test_case.skills is None:
                    # Load skills from the test fixture directory plus any
                    # extra paths (pre-loaded skills / replay tempdir)
                    skills = load_skill_catalog(
                        custom_skill_paths=[
                            test_case.folder,
                            *(additional_skill_paths or []),
                        ]
                    )
                elif test_case.skills == {}:
                    skills = None
                else:
                    try:
                        skills = SkillCatalog(**test_case.skills)
                    except Exception as e:
                        raise ValueError(
                            f"Failed to convert skills dict to SkillCatalog: {e}. "
                            f"Expected format: {{'skills': [...]}}, got: {test_case.skills}"
                        ) from e
                messages = build_initial_ask_messages(
                    initial_user_prompt=user_prompt,
                    file_paths=None,
                    tool_executor=ai.tool_executor,
                    skills=skills,
                    system_prompt_additions=additional_system_prompt,
                    cluster_name=test_case.cluster_name,
                    prompt_component_overrides=prompt_component_overrides,
                )
        else:
            chat_request = ChatRequest(
                ask=user_prompt,
                additional_system_prompt=additional_system_prompt,
            )
            config = Config()
            if test_case.cluster_name:
                config.cluster_name = test_case.cluster_name

            dal = load_test_dal(
                Path(test_case.folder), initialize_base=False
            )
            skills = load_skill_catalog(dal=dal)
            global_instructions = dal.get_global_instructions_for_account()

            messages = build_chat_messages(
                ask=chat_request.ask,
                conversation_history=test_case.conversation_history,
                ai=ai,
                config=config,
                global_instructions=global_instructions,
                skills=skills,
                additional_system_prompt=additional_system_prompt,
                prompt_component_overrides=prompt_component_overrides,
            )

        # Create LLM completion trace within current context
        with tracer.start_trace("Holmes Run", span_type=SpanType.TASK) as llm_span:
            start_time = time.time()
            result = ai.call(messages=messages, trace_span=llm_span)
            holmes_duration = time.time() - start_time
            # Log duration directly to eval_span
            eval_span.log(metadata={"holmes_duration": holmes_duration})
            # Store metrics in user_properties for GitHub report
            if request:
                request.node.user_properties.append(
                    ("holmes_duration", holmes_duration)
                )
                if result.num_llm_calls is not None:
                    request.node.user_properties.append(
                        ("num_llm_calls", result.num_llm_calls)
                    )
                if result.tool_calls is not None:
                    request.node.user_properties.append(
                        ("tool_call_count", len(result.tool_calls))
                    )
                    # Bash commands HolmesGPT tried to run but were denied (the eval
                    # has no interactive approver and the bash toolset enforces an
                    # allow/deny list). Surfaced as a column in the eval report.
                    denied_commands = extract_denied_commands(result.tool_calls)
                    if denied_commands:
                        request.node.user_properties.append(
                            ("denied_commands", denied_commands)
                        )

        return result
