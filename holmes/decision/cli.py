"""CLI for the fork's LLM + Jev + evidence-memory investigation mode."""

import json
import os
from pathlib import Path
from typing import Optional

import typer

from holmes.common.env_vars import DEFAULT_CLI_USER
from holmes.config import Config
from holmes.core.oauth_utils import enable_disk_token_store
from holmes.decision.demo import DemoDecisionProvider, DemoReasoner, DemoTools
from holmes.decision.engine import DecisionAgent
from holmes.decision.models import Budget, DecisionState, InvestigationResult
from holmes.decision.providers import HolmesReasoner, JevDecisionProvider
from holmes.decision.store import EvidenceStore
from holmes.decision.tools import HolmesTools


decision_app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False,
                          help="Investigate with an LLM, Jev decisions and durable evidence.")


def _store(url: str) -> EvidenceStore:
    if not url:
        raise typer.BadParameter("Set HOLMES_DECISION_DATABASE_URL (PostgreSQL) or pass --database-url")
    store = EvidenceStore.from_url(url)
    store.initialize()
    return store


def _print_result(result: InvestigationResult, as_json: bool) -> None:
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    state = result.state
    typer.echo(f"Task: {state.task_id}\nStatus: {state.status} ({state.stop_reason})")
    typer.echo(result.diagnosis.summary)
    for cause in result.diagnosis.causes:
        typer.echo(f"- {cause.text} [{', '.join(cause.evidence_ids)}]")
    for step in result.diagnosis.verification_steps:
        typer.echo(f"Verify: {step}")
    for limitation in result.diagnosis.limitations:
        typer.echo(f"Limitation: {limitation}")
    typer.echo(f"Tool calls: {state.tool_calls}; reasoning calls: {state.reasoning_calls} (+ final report)")


@decision_app.command()
def investigate(
    question: str = typer.Argument(..., help="Symptom, affected service and time window"),
    config_file: Optional[Path] = typer.Option(None, "--config", exists=True),
    model: Optional[str] = typer.Option(None, "--model"),
    database_url: str = typer.Option("", envvar="HOLMES_DECISION_DATABASE_URL", show_default=False),
    jev_model: str = typer.Option("jev-latest", envvar="HOLMES_JEV_MODEL"),
    allow_tool: Optional[list[str]] = typer.Option(None, "--allow-tool", help="Repeat to restrict this session's tool names"),
    max_steps: int = typer.Option(20, min=1, max=100),
    max_tool_calls: int = typer.Option(10, min=1, max=100),
    max_reasoning_calls: int = typer.Option(5, min=1, max=30),
    max_seconds: float = typer.Option(300, min=1, max=3600),
    min_confidence: float = typer.Option(0.65, min=0, max=1),
    events: bool = typer.Option(False, help="Print durable progress events to stderr as JSONL"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run against explicitly enabled Holmes/MCP tools and real Jev/LLM services."""
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise typer.BadParameter("Set TYPESAFE_API_KEY to use Jev, or run 'holmes decision demo'")
    provider = JevDecisionProvider(key, model=jev_model)
    store = _store(database_url)
    try:
        config = Config.load_from_file(config_file, model=model)
        enable_disk_token_store()
        llm = config._get_llm(model_key=model)
        llm.args.setdefault("timeout", 60)
        llm.args.setdefault("num_retries", 1)
        gateway = HolmesTools(config, llm, DEFAULT_CLI_USER,
                              set(allow_tool) if allow_tool else None)
        agent = DecisionAgent(
            HolmesReasoner(llm), provider, gateway, store,
            on_event=(lambda event: typer.echo(json.dumps(event), err=True)) if events else None,
        )
        result = agent.run(DecisionState(question=question, budget=Budget(
            max_steps=max_steps, max_tool_calls=max_tool_calls,
            max_reasoning_calls=max_reasoning_calls, max_seconds=max_seconds,
            min_confidence=min_confidence,
        )))
        _print_result(result, as_json)
        if result.state.status == "failed":
            raise typer.Exit(1)
    finally:
        store.engine.dispose()


@decision_app.command()
def demo(
    database_url: str = typer.Option("sqlite:////tmp/holmes-decision-demo.db", show_default=True),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Exercise the loop and persistence with simulated data and no API keys."""
    store = _store(database_url)
    try:
        result = DecisionAgent(DemoReasoner(), DemoDecisionProvider(), DemoTools(), store).run(
            DecisionState(question="演示 checkout 接口超时排障", context={"mode": "offline_simulation"})
        )
        _print_result(result, as_json)
    finally:
        store.engine.dispose()


@decision_app.command()
def evidence(
    task_id: str, evidence_id: str,
    database_url: str = typer.Option("", envvar="HOLMES_DECISION_DATABASE_URL", show_default=False),
    offset: int = typer.Option(0, min=0),
    limit: int = typer.Option(4000, min=1, max=12000),
) -> None:
    """Read a bounded page of a raw result; evidence is scoped to the task."""
    store = _store(database_url)
    try:
        typer.echo(json.dumps(store.read_evidence(task_id, evidence_id, offset, limit), ensure_ascii=False, indent=2))
    finally:
        store.engine.dispose()


@decision_app.command()
def inspect(
    task_id: str,
    database_url: str = typer.Option("", envvar="HOLMES_DECISION_DATABASE_URL", show_default=False),
) -> None:
    """Inspect persisted task memory, decisions, and diagnosis after a restart."""
    store = _store(database_url)
    try:
        state = store.load(task_id)
        diagnosis = store.load_diagnosis(task_id)
        typer.echo(json.dumps({
            "state": state.model_dump(mode="json"),
            "diagnosis": diagnosis.model_dump() if diagnosis else None,
            "events": store.audit(task_id),
        }, ensure_ascii=False, indent=2))
    finally:
        store.engine.dispose()
