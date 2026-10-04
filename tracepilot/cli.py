"""Standalone CLI without loading the optional integration stack."""

import json
from pathlib import Path

import typer

from tracepilot import __version__
from tracepilot.config import AgentConfig
from tracepilot.engine import DecisionAgent
from tracepilot.llm import HTTPCompletion
from tracepilot.mcp_gateway import MCPGateway, required_environment
from tracepilot.models import DecisionState, InvestigationResult, InvestigationScope
from tracepilot.providers import JevDecisionProvider, StructuredReasoner
from tracepilot.scenarios import ScenarioDecisionProvider, ScenarioReasoner, ScenarioTools, demo_scope
from tracepilot.store import EvidenceStore


app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False,
                  help="TracePilot — evidence-driven service incident investigations")


def open_store(url: str) -> EvidenceStore:
    store = EvidenceStore.from_url(url)
    store.initialize()
    return store


def print_result(result: InvestigationResult, as_json: bool) -> None:
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    typer.echo(f"TracePilot {__version__} | Task {result.state.task_id}")
    typer.echo(f"Status: {result.state.status} ({result.state.stop_reason})")
    typer.echo(result.diagnosis.summary)
    for cause in result.diagnosis.causes:
        typer.echo(f"- {cause.text} [{', '.join(cause.evidence_ids)}]")
    for step in result.diagnosis.verification_steps:
        typer.echo(f"Verify: {step}")
    for limitation in result.diagnosis.limitations:
        typer.echo(f"Limitation: {limitation}")
    typer.echo(f"Tools: {result.state.tool_calls}; reasoning: {result.state.reasoning_calls} (+ report)")


@app.command()
def investigate(question: str, config: Path = typer.Option(..., exists=True, dir_okay=False),
                service: str = typer.Option(...), namespace: str = typer.Option(...),
                start: str = typer.Option(...), end: str = typer.Option(...),
                database_url: str = typer.Option("sqlite:///tracepilot.db", envvar="TRACEPILOT_DATABASE_URL"),
                events: bool = typer.Option(False), as_json: bool = typer.Option(False, "--json")) -> None:
    """Run real LLM + Jev inference against explicitly permitted MCP tools."""
    settings = AgentConfig.load(config)
    scope = InvestigationScope(service=service, namespace=namespace, start=start, end=end)
    provider = JevDecisionProvider(required_environment(settings.jev_api_key_env), settings.jev_model)
    llm = HTTPCompletion(settings.llm)
    store = open_store(database_url)
    try:
        result = DecisionAgent(StructuredReasoner(llm), provider, MCPGateway(settings.mcp_servers, scope), store,
                               on_event=(lambda event: typer.echo(json.dumps(event), err=True)) if events else None).run(
            DecisionState(question=question, scope=scope, budget=settings.budget, context={"mode": "live", "product": "TracePilot"}))
        print_result(result, as_json)
        if result.state.status == "failed":
            raise typer.Exit(1)
    finally:
        store.engine.dispose()


@app.command()
def demo(scenario: str = typer.Option("db-pool", help="db-pool / slow-dependency / no-data / denied"),
         database_url: str = typer.Option("sqlite:///tracepilot-demo.db"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Run a simulated, evidence-dependent diagnostic scenario without API keys."""
    gateway = ScenarioTools(scenario)
    store = open_store(database_url)
    try:
        result = DecisionAgent(ScenarioReasoner(), ScenarioDecisionProvider(), gateway, store).run(
            DecisionState(question="排查 checkout 服务接口超时", scope=demo_scope(), context={"mode": "offline_simulation", "product": "TracePilot"}))
        print_result(result, as_json)
    finally:
        store.engine.dispose()


@app.command()
def inspect(task_id: str, database_url: str = typer.Option("sqlite:///tracepilot-demo.db", envvar="TRACEPILOT_DATABASE_URL")) -> None:
    """Read persisted memory, events, evidence summaries and diagnosis."""
    store = open_store(database_url)
    try:
        state = store.load(task_id)
        diagnosis = store.load_diagnosis(task_id)
        typer.echo(json.dumps({"state": state.model_dump(mode="json"), "diagnosis": diagnosis.model_dump() if diagnosis else None,
                               "events": store.audit(task_id), "integrity": store.verify(task_id)}, ensure_ascii=False, indent=2))
    finally:
        store.engine.dispose()


@app.command()
def evidence(task_id: str, evidence_id: str,
             database_url: str = typer.Option("sqlite:///tracepilot-demo.db", envvar="TRACEPILOT_DATABASE_URL"),
             offset: int = typer.Option(0, min=0), limit: int = typer.Option(4000, min=1, max=12000)) -> None:
    """Read a bounded page of original tool output within its owning task."""
    store = open_store(database_url)
    try:
        typer.echo(json.dumps(store.read_evidence(task_id, evidence_id, offset, limit), ensure_ascii=False, indent=2))
    finally:
        store.engine.dispose()


@app.command()
def verify(task_id: str, database_url: str = typer.Option("sqlite:///tracepilot-demo.db", envvar="TRACEPILOT_DATABASE_URL")) -> None:
    """Check evidence payload and metadata against persisted checkpoint digests."""
    store = open_store(database_url)
    try:
        result = store.verify(task_id)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["valid"]:
            raise typer.Exit(1)
    finally:
        store.engine.dispose()
