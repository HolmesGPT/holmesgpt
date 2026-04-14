"""
Public API v1 router for HolmesGPT.

Provides a stable, versioned API surface for external integrations and
programmatic access.  All endpoints live under /api/v1/ and are
authenticated by the existing OktaAuthMiddleware (JWT or hgpt_ API key).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Security scheme — makes Swagger show "Authorize" button
_bearer_scheme = HTTPBearer(
    description="Paste an Okta JWT or hgpt_ API key",
    auto_error=False,  # Don't error here — OktaAuthMiddleware handles auth
)

router = APIRouter(
    prefix="/api/v1",
    tags=["Public API v1"],
    dependencies=[Depends(_bearer_scheme)],
)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class InvestigateRequest(BaseModel):
    """Request body for one-shot investigation."""

    ask: str = Field(..., description="The question or issue to investigate.")
    project_id: Optional[str] = Field(
        default=None,
        description="Scope the investigation to a specific project.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default LLM model for this request.",
    )


class ToolCallOut(BaseModel):
    """Summary of a single tool call made during an investigation."""

    tool_call_id: str
    tool_name: str
    description: str


class InvestigateResponse(BaseModel):
    """Response for a one-shot investigation."""

    analysis: str = Field(..., description="Holmes's analysis / answer.")
    tool_calls: List[ToolCallOut] = Field(
        default_factory=list,
        description="Tools that were called during the investigation.",
    )


class ChatStreamRequest(BaseModel):
    """Request body for streaming conversation."""

    ask: str = Field(..., description="The question or follow-up message.")
    project_id: Optional[str] = Field(
        default=None,
        description="Scope the conversation to a specific project.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default LLM model for this request.",
    )
    conversation_history: Optional[list[dict]] = Field(
        default=None,
        description="Previous conversation messages for multi-turn chats.",
    )


class InvestigationSummary(BaseModel):
    """Lightweight summary returned when listing investigations."""

    id: str
    started_at: str = ""
    question: str = ""
    answer_preview: str = Field(
        "",
        description="First 500 characters of the answer.",
    )
    project_id: str = ""
    source: str = ""
    status: str = ""
    feedback: Optional[str] = None


class InvestigationDetail(BaseModel):
    """Full investigation record."""

    id: str
    started_at: str = ""
    finished_at: str = ""
    trigger: str = ""
    source: str = ""
    source_id: str = ""
    source_url: str = ""
    question: str = ""
    answer: str = ""
    project_id: str = ""
    status: str = ""
    error: str = ""
    feedback: Optional[str] = None
    resolution_summary: Optional[str] = None
    metadata: dict = {}


class SimilarInvestigation(BaseModel):
    """A past investigation that is similar to a query."""

    id: str
    question: str = ""
    answer_summary: str = ""
    source: str = ""
    started_at: str = ""
    score: float = 0.0
    feedback: Optional[str] = None
    resolution_summary: Optional[str] = None


class ModelsResponse(BaseModel):
    """List of available LLM model names."""

    models: List[str] = Field(
        default_factory=list,
        description="Available model identifiers.",
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _check_project_scope(request: Request, project_id: Optional[str]) -> None:
    """Raise 403 if the caller's API key does not cover *project_id*."""
    if not project_id:
        return
    from rbac import check_api_key_project_access  # noqa: PLC0415

    perms = getattr(request.state, "permissions", None)
    if perms and not check_api_key_project_access(perms, project_id):
        raise HTTPException(
            status_code=403,
            detail=f"API key does not have access to project '{project_id}'",
        )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


# -- Models ----------------------------------------------------------------


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="List available LLM models",
    description="Returns the list of model identifiers configured in this deployment.",
)
def list_models() -> ModelsResponse:
    import server as original_server  # noqa: PLC0415

    models = original_server.config.get_models_list()
    return ModelsResponse(models=models)


# -- Investigate (one-shot) ------------------------------------------------


@router.post(
    "/investigate",
    response_model=InvestigateResponse,
    summary="Run a one-shot investigation",
    description=(
        "Submit a question and receive Holmes's analysis.  "
        "The request blocks until the investigation is complete."
    ),
)
def investigate(body: InvestigateRequest, request: Request) -> InvestigateResponse:
    _check_project_scope(request, body.project_id)

    import server as original_server  # noqa: PLC0415
    from holmes.core.models import ChatRequest  # noqa: PLC0415

    chat_request = ChatRequest(
        ask=body.ask,
        model=body.model,
        project_id=body.project_id,
        stream=False,
    )
    response = original_server.chat(chat_request, request)

    tool_calls_out = []
    if response.tool_calls:
        for tc in response.tool_calls:
            tool_calls_out.append(
                ToolCallOut(
                    tool_call_id=tc.tool_call_id,
                    tool_name=tc.tool_name,
                    description=tc.description,
                )
            )

    return InvestigateResponse(
        analysis=response.analysis,
        tool_calls=tool_calls_out,
    )


# -- Chat (streaming) -----------------------------------------------------


@router.post(
    "/chat",
    summary="Streaming conversation (SSE)",
    description=(
        "Submit a question or follow-up and receive a Server-Sent Events stream.  "
        "Pass conversation_history for multi-turn conversations."
    ),
)
def chat_stream(body: ChatStreamRequest, request: Request):
    _check_project_scope(request, body.project_id)

    import server as original_server  # noqa: PLC0415
    from holmes.core.models import ChatRequest  # noqa: PLC0415

    chat_request = ChatRequest(
        ask=body.ask,
        model=body.model,
        project_id=body.project_id,
        stream=True,
        conversation_history=body.conversation_history,
    )
    return original_server.chat(chat_request, request)


# -- Investigations --------------------------------------------------------


@router.get(
    "/investigations",
    response_model=List[InvestigationSummary],
    summary="List past investigations",
    description="Return investigation summaries sorted by newest first.",
)
def list_investigations(
    request: Request,
    project_id: Optional[str] = Query(default=None, description="Filter by project."),
    limit: int = Query(default=50, ge=1, le=200, description="Max results."),
    source: Optional[str] = Query(default=None, description="Filter by source (e.g. 'pagerduty', 'ui')."),
):
    _check_project_scope(request, project_id)

    from projects import get_investigation_store  # noqa: PLC0415

    investigations = get_investigation_store().list(
        limit=limit,
        source=source,
        project_id=project_id,
    )

    summaries = []
    for inv in investigations:
        answer_preview = inv.answer[:500] if inv.answer else ""
        if len(inv.answer) > 500:
            answer_preview = answer_preview[:497] + "..."
        summaries.append(
            InvestigationSummary(
                id=inv.id,
                started_at=inv.started_at,
                question=inv.question,
                answer_preview=answer_preview,
                project_id=inv.project_id,
                source=inv.source,
                status=inv.status,
                feedback=inv.feedback,
            )
        )
    return summaries


@router.get(
    "/investigations/similar",
    response_model=List[SimilarInvestigation],
    summary="Search similar investigations",
    description="Find past investigations that are similar to a given query.",
)
def search_similar(
    request: Request,
    query: str = Query(..., description="The search query to find similar investigations."),
    project_id: Optional[str] = Query(default=None, description="Scope to a specific project."),
    limit: int = Query(default=3, ge=1, le=20, description="Max results."),
):
    _check_project_scope(request, project_id)

    from projects import get_investigation_store  # noqa: PLC0415

    results = get_investigation_store().search_similar(
        query=query,
        project_id=project_id,
        limit=limit,
    )

    return [
        SimilarInvestigation(
            id=r["id"],
            question=r.get("question", ""),
            answer_summary=r.get("answer_summary", ""),
            source=r.get("source", ""),
            started_at=r.get("started_at", ""),
            score=r.get("score", 0.0),
            feedback=r.get("feedback"),
            resolution_summary=r.get("resolution_summary"),
        )
        for r in results
    ]


@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationDetail,
    summary="Get a single investigation",
    description="Retrieve full details of a past investigation by ID.",
)
def get_investigation(investigation_id: str, request: Request):
    from projects import get_investigation_store  # noqa: PLC0415

    inv = get_investigation_store().get(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")

    # Check project scope on the result's project_id
    _check_project_scope(request, inv.project_id or None)

    return InvestigationDetail(
        id=inv.id,
        started_at=inv.started_at,
        finished_at=inv.finished_at,
        trigger=inv.trigger,
        source=inv.source,
        source_id=inv.source_id,
        source_url=inv.source_url,
        question=inv.question,
        answer=inv.answer,
        project_id=inv.project_id,
        status=inv.status,
        error=inv.error,
        feedback=inv.feedback,
        resolution_summary=inv.resolution_summary,
        metadata=inv.metadata,
    )
