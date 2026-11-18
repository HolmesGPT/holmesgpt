"""The committed eval model list (tests/llm/model_list.yaml) and how evals resolve models from it."""

import re
from pathlib import Path

import pytest
import yaml

import tests.llm.utils.test_case_utils as test_case_utils
from holmes.config import Config
from holmes.utils.env import ENV_PLACEHOLDER_PATTERN

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_LIST = REPO_ROOT / "tests" / "llm" / "model_list.yaml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
VERIFY_ACTION = REPO_ROOT / ".github" / "actions" / "verify-models" / "action.yml"
# The MODEL repo variable: eval-regression.yaml's default when no model is requested.
REGRESSION_DEFAULT_MODEL = "opus-4.6"
# Steps that only read the list's keys and never call a model.
KEYLESS_STEPS = {"Collect evals to run"}
CREDENTIAL_FIELDS = {
    "api_key",
    "api_base",
    "base_url",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_bearer_token",
}
SECRET_PLACEHOLDER = re.compile(r"^\{\{ env\.(EVALS_[A-Z0-9_]+) \}\}$")


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def model_list() -> dict:
    return load_yaml(MODEL_LIST)


def placeholder_vars() -> set[str]:
    values = yaml.safe_dump(model_list())
    return {name.strip() for name in ENV_PLACEHOLDER_PATTERN.findall(values)}


def workflow_steps():
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for job in (load_yaml(path).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                yield path.name, step


def csv(value: str) -> list[str]:
    return [m.strip() for m in value.split(",") if m.strip()]


def workflow_default_models() -> set[str]:
    benchmarks = load_yaml(WORKFLOWS / "eval-benchmarks.yaml")
    master = load_yaml(WORKFLOWS / "eval-master.yaml")
    dispatch_inputs = benchmarks[True]["workflow_dispatch"]["inputs"]
    models = {REGRESSION_DEFAULT_MODEL}
    models.update(csv(benchmarks["env"]["DEFAULT_BENCHMARK_MODELS"]))
    models.update(csv(dispatch_inputs["models"]["default"]))
    models.update(csv(master["env"]["DEFAULT_MASTER_MODELS"]))
    for path in [*WORKFLOWS.glob("*.y*ml"), VERIFY_ACTION]:
        models.update(
            re.findall(r"CLASSIFIER_MODEL \|\| '([^']+)'", path.read_text())
        )
    return models


def test_every_workflow_default_model_is_defined():
    missing = workflow_default_models() - set(model_list())
    assert not missing, f"Not defined in {MODEL_LIST.name}: {sorted(missing)}"


@pytest.mark.parametrize("name", list(model_list()))
def test_credentials_are_evals_placeholders(name):
    entry = model_list()[name]
    credential_fields = {
        key: value
        for key, value in entry.items()
        if key in CREDENTIAL_FIELDS
    }
    assert credential_fields, f"{name} has no credentials"
    for key, value in credential_fields.items():
        assert SECRET_PLACEHOLDER.match(str(value)), (
            f"{name}.{key} must be a '{{{{ env.EVALS_* }}}}' placeholder, not a value"
        )


def test_workflows_pass_every_key_the_list_needs():
    needed = placeholder_vars()
    checked = 0
    for workflow, step in workflow_steps():
        env = step.get("env") or {}
        location = env.get("MODEL_LIST_FILE_LOCATION")
        if location is None:
            continue
        checked += 1
        where = f"{workflow} / {step.get('name')}"
        assert str(location).endswith("/tests/llm/model_list.yaml"), where
        if step.get("name") in KEYLESS_STEPS:
            assert not needed & set(env), f"{where} reads keys only, pass no secrets"
            continue
        for var in sorted(needed):
            assert env.get(var) == f"${{{{ secrets.{var} }}}}", f"{where}: {var}"
    assert checked >= 7


def test_no_workflow_reads_the_old_secret():
    for path in [*WORKFLOWS.glob("*.y*ml"), VERIFY_ACTION]:
        text = path.read_text()
        assert "MODEL_LIST_YAML" not in text, path.name
        assert "/tmp/model_list.yaml" not in text, path.name


@pytest.fixture
def committed_list(monkeypatch):
    monkeypatch.setattr("holmes.core.llm.MODEL_LIST_FILE_LOCATION", str(MODEL_LIST))
    monkeypatch.setattr(test_case_utils, "MODEL_LIST_FILE_LOCATION", str(MODEL_LIST))
    monkeypatch.delenv("MODEL", raising=False)
    for var in placeholder_vars():
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_every_model_loads_with_all_keys_set(committed_list):
    for var in placeholder_vars():
        committed_list.setenv(var, f"https://{var.lower()}.invalid")

    registry = Config().llm_model_registry

    assert registry.unavailable_models == {}
    assert list(registry.models) == list(model_list())


def test_one_key_loads_only_the_models_it_serves(committed_list):
    committed_list.setenv("EVALS_OPENROUTER_API_KEY", "or-key")

    registry = Config().llm_model_registry

    openrouter = [
        name
        for name, entry in model_list().items()
        if entry["model"].startswith("openrouter/")
    ]
    assert sorted(registry.models) == sorted(openrouter)
    assert set(registry.unavailable_models) == set(model_list()) - set(openrouter)


def test_models_from_model_list_include_unavailable(committed_list):
    committed_list.setenv("EVALS_OPENROUTER_API_KEY", "or-key")

    models = test_case_utils._get_models_from_model_list()

    assert sorted(models) == sorted(model_list())


def test_get_models_accepts_a_defined_model_without_its_key(committed_list):
    # Collecting evals needs no keys; the run fails when it builds the LLM.
    committed_list.setattr(test_case_utils, "MODEL", "opus-5.5-medium")

    assert test_case_utils.get_models() == ["opus-5.5-medium"]


def test_get_models_rejects_an_undefined_model(committed_list):
    committed_list.setattr(test_case_utils, "MODEL", "gpt-4.1")

    with pytest.raises(ValueError, match="not defined in the model list: gpt-4.1"):
        test_case_utils.get_models()


def test_create_eval_llm_returns_the_requested_model(committed_list):
    committed_list.setenv("EVALS_OPENROUTER_API_KEY", "or-key")

    llm = test_case_utils.create_eval_llm("kimi-k3")

    assert llm.name == "kimi-k3"
    assert llm.model == "openrouter/moonshotai/kimi-k3"
    assert llm.api_key == "or-key"
    assert llm.args["extra_body"]["provider"]["allow_fallbacks"] is False


def test_create_eval_llm_names_missing_keys(committed_list):
    committed_list.setenv("EVALS_OPENROUTER_API_KEY", "or-key")

    with pytest.raises(ValueError) as exc:
        test_case_utils.create_eval_llm("gpt-6.1-sol-medium")

    assert str(exc.value) == (
        f"Model 'gpt-6.1-sol-medium' is defined in {MODEL_LIST} but was not loaded: "
        "environment variable(s) EVALS_AZURE_SWEDEN_API_BASE, "
        "EVALS_AZURE_SWEDEN_API_KEY not set"
    )


def test_create_eval_llm_rejects_an_undefined_model(committed_list):
    # Without this the registry serves its first model under the requested name.
    committed_list.setenv("EVALS_OPENROUTER_API_KEY", "or-key")

    with pytest.raises(ValueError) as exc:
        test_case_utils.create_eval_llm("gpt-4.1")

    assert f"Model 'gpt-4.1' is not defined in {MODEL_LIST}" in str(exc.value)
    assert "Available models: deepseek-v4-pro, kimi-k3, glm-5.3" in str(exc.value)
