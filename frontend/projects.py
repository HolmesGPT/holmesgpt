"""
Projects and LLM instructions persistence backed by DynamoDB.

Table schema (single-table design):
  pk                    | sk              | data / instructions
  PROJECT#<id>          | META            | JSON-serialised Project
  INSTANCE#<id>         | META            | JSON-serialised Instance
  LLM_OVERRIDE          | <toolset_name>  | instructions string
  TOOLSET_STATE         | <toolset_name>  | enabled bool
  INVESTIGATION#<id>    | META            | JSON-serialised Investigation
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import boto3
from boto3.dynamodb.conditions import Key
from pydantic import BaseModel, Field

TABLE_NAME = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


# ── Data models ────────────────────────────────────────────────────────────────


class ToolsetInstance(BaseModel):
    """One integration instance within a project (legacy model kept for backwards compat)."""

    type: str  # base toolset type: "grafana/dashboards", "aws_api", "salesforce", "ado", "atlassian"
    name: str  # unique instance name: "grafana-logistics", "aws_api"
    secret_arn: Optional[str] = None  # Secrets Manager ARN for per-instance credentials
    # For MCP toolsets: override the MCP server URL (leave None to use global URL)
    mcp_url: Optional[str] = None
    # For aws_api: restrict to these account profile names (None = all configured accounts)
    aws_accounts: Optional[list[str]] = None


class TagFilter(BaseModel):
    """Tag-based filter that determines which instances a project uses."""

    logic: Literal["AND", "OR"] = "AND"
    tags: dict[str, str] = {}


class Instance(BaseModel):
    """Top-level instance resource with free-form tags for routing."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: str  # base toolset type: "grafana/dashboards", "aws_api", "salesforce", "ado", "atlassian"
    name: str  # unique instance name: "grafana-logistics", "aws_api"
    tags: dict[
        str, str
    ] = {}  # free-form key-value tags; empty = global (always included)
    secret_arn: Optional[str] = None
    mcp_url: Optional[str] = None
    aws_accounts: Optional[list[str]] = None
    # AWS cross-account fields
    aws_account_name: Optional[str] = None
    aws_account_id: Optional[str] = None
    aws_role_arn: Optional[str] = None
    aws_connection_status: Optional[str] = (
        None  # "success", "error", or None (untested)
    )
    aws_connection_error: Optional[str] = None
    created_at: str = ""


class WebhookRouting(BaseModel):
    """Webhook routing rules: maps external source identifiers to this project."""

    ado: list[str] = []  # ADO team project names (System.TeamProject)
    pagerduty: list[str] = []  # PagerDuty service names or IDs
    salesforce: list[str] = []  # Salesforce account names or IDs


class Project(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    description: str = ""
    tag_filter: Optional[TagFilter] = None  # None = only global (untagged) instances
    webhook_write_back: Optional[dict[str, bool]] = (
        None  # per-webhook write-back overrides; None/missing key = inherit global
    )
    webhook_routing: Optional[WebhookRouting] = None
    created_at: str = ""


# ── Tag matching logic ─────────────────────────────────────────────────────────


def match_instance(instance: Instance, tag_filter: TagFilter) -> bool:
    """
    Return True if *instance* should be included given *tag_filter*.

    Rules:
    - Untagged instance (empty tags dict) → always True (global instance)
    - Empty tag_filter.tags → only global instances match (tagged instances excluded)
    - AND logic → all filter key=value pairs must match instance tags
    - OR logic → at least one filter key=value pair must match instance tags
    """
    if not instance.tags:
        return True  # untagged = global, always included
    if not tag_filter.tags:
        return False  # empty filter only matches globals
    if tag_filter.logic == "AND":
        return all(instance.tags.get(k) == v for k, v in tag_filter.tags.items())
    else:  # OR
        return any(instance.tags.get(k) == v for k, v in tag_filter.tags.items())


def resolve_instances_for_project(
    project: Project, all_instances: list[Instance]
) -> list[Instance]:
    """
    Return the list of instances that should be active for *project*.

    - If project.tag_filter is None → only global (untagged) instances
    - Otherwise → all instances where match_instance() returns True
    """
    if project.tag_filter is None:
        return [i for i in all_instances if not i.tags]
    return [i for i in all_instances if match_instance(i, project.tag_filter)]


def resolve_project_for_webhook(source: str, identifier: str) -> Optional["Project"]:
    """Find the project whose webhook_routing matches the given source and identifier.

    Args:
        source: Webhook source type ("ado", "pagerduty", "salesforce")
        identifier: The extracted identifier from the webhook payload

    Returns:
        Matching Project, or None (use global instances)
    """
    if not identifier:
        return None
    identifier_lower = identifier.strip().lower()
    for project in get_store().list():
        routing = project.webhook_routing
        if not routing:
            continue
        candidates = getattr(routing, source, [])
        if any(c.strip().lower() == identifier_lower for c in candidates):
            return project
    return None


# ── DynamoDB helpers ───────────────────────────────────────────────────────────


def _get_table():
    return boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)


# ── Instances store ─────────────────────────────────────────────────────────────


class InstancesStore:
    def create(
        self,
        type: str,
        name: str,
        tags: dict[str, str] = {},
        secret_arn: Optional[str] = None,
        mcp_url: Optional[str] = None,
        aws_accounts: Optional[list[str]] = None,
        aws_account_name: Optional[str] = None,
        aws_account_id: Optional[str] = None,
        aws_role_arn: Optional[str] = None,
    ) -> Instance:
        inst = Instance(
            type=type,
            name=name,
            tags=tags,
            secret_arn=secret_arn,
            mcp_url=mcp_url,
            aws_accounts=aws_accounts,
            aws_account_name=aws_account_name,
            aws_account_id=aws_account_id,
            aws_role_arn=aws_role_arn,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _get_table().put_item(
            Item={
                "pk": f"INSTANCE#{inst.id}",
                "sk": "META",
                "data": inst.model_dump_json(),
            }
        )
        return inst

    def update(self, instance_id: str, **kwargs) -> Optional[Instance]:
        inst = self.get(instance_id)
        if not inst:
            return None
        for k, v in kwargs.items():
            setattr(inst, k, v)
        _get_table().put_item(
            Item={
                "pk": f"INSTANCE#{inst.id}",
                "sk": "META",
                "data": inst.model_dump_json(),
            }
        )
        return inst

    def delete(self, instance_id: str) -> bool:
        resp = _get_table().delete_item(
            Key={"pk": f"INSTANCE#{instance_id}", "sk": "META"},
            ReturnValues="ALL_OLD",
        )
        return bool(resp.get("Attributes"))

    def get(self, instance_id: str) -> Optional[Instance]:
        resp = _get_table().get_item(
            Key={"pk": f"INSTANCE#{instance_id}", "sk": "META"}
        )
        item = resp.get("Item")
        if not item:
            return None
        return Instance.model_validate_json(item["data"])

    def list(self) -> list[Instance]:
        from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

        table = _get_table()
        filter_expr = Attr("pk").begins_with("INSTANCE#") & Attr("sk").eq("META")
        items: list = []
        kwargs: dict = {"FilterExpression": filter_expr}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        instances = [Instance.model_validate_json(item["data"]) for item in items]
        return sorted(instances, key=lambda i: i.created_at)


_instances_store = InstancesStore()


def get_instances_store() -> InstancesStore:
    return _instances_store


# ── Duplicate routing validation ───────────────────────────────────────────────


def _check_duplicate_routing(
    webhook_routing: Optional[WebhookRouting],
    exclude_project_id: Optional[str] = None,
) -> None:
    """Raise ValueError if any routing value is already claimed by another project."""
    if not webhook_routing:
        return
    for project in get_store().list():
        if exclude_project_id and project.id == exclude_project_id:
            continue
        if not project.webhook_routing:
            continue
        for source in ("ado", "pagerduty", "salesforce"):
            existing = {
                v.strip().lower() for v in getattr(project.webhook_routing, source, [])
            }
            incoming = {v.strip().lower() for v in getattr(webhook_routing, source, [])}
            overlap = existing & incoming
            if overlap:
                raise ValueError(
                    f"{source} routing value(s) {overlap} already claimed by project '{project.name}'"
                )


# ── Projects store ─────────────────────────────────────────────────────────────


class ProjectsStore:
    def create(
        self,
        name: str,
        description: str,
        tag_filter: Optional[dict] = None,
        webhook_routing: Optional[dict] = None,
    ) -> Project:
        _check_duplicate_routing(
            WebhookRouting(**webhook_routing) if webhook_routing else None
        )
        p = Project(
            name=name,
            description=description,
            tag_filter=TagFilter(**tag_filter) if tag_filter else None,
            webhook_routing=WebhookRouting(**webhook_routing)
            if webhook_routing
            else None,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _get_table().put_item(
            Item={"pk": f"PROJECT#{p.id}", "sk": "META", "data": p.model_dump_json()}
        )
        return p

    def update(self, project_id: str, **kwargs) -> Optional[Project]:
        p = self.get(project_id)
        if not p:
            return None
        for k, v in kwargs.items():
            if k == "tag_filter":
                v = TagFilter(**v) if v else None
            elif k == "webhook_routing":
                v = WebhookRouting(**v) if v else None
            setattr(p, k, v)
        _check_duplicate_routing(p.webhook_routing, exclude_project_id=project_id)
        _get_table().put_item(
            Item={"pk": f"PROJECT#{p.id}", "sk": "META", "data": p.model_dump_json()}
        )
        return p

    def delete(self, project_id: str) -> bool:
        resp = _get_table().delete_item(
            Key={"pk": f"PROJECT#{project_id}", "sk": "META"},
            ReturnValues="ALL_OLD",
        )
        return bool(resp.get("Attributes"))

    def get(self, project_id: str) -> Optional[Project]:
        resp = _get_table().get_item(Key={"pk": f"PROJECT#{project_id}", "sk": "META"})
        item = resp.get("Item")
        if not item:
            return None
        return Project.model_validate_json(item["data"])

    def list(self) -> list[Project]:
        # Scan for all PROJECT# items with pagination support
        from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

        table = _get_table()
        filter_expr = Attr("pk").begins_with("PROJECT#") & Attr("sk").eq("META")
        items: list = []
        kwargs: dict = {"FilterExpression": filter_expr}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        projects = [Project.model_validate_json(item["data"]) for item in items]
        return sorted(projects, key=lambda p: p.created_at)


_store = ProjectsStore()


def get_store() -> ProjectsStore:
    return _store


# ── LLM instructions store ─────────────────────────────────────────────────────


class LLMInstructionsStore:
    def load_all(self) -> dict[str, str]:
        """Return {toolset_name: instructions} for all stored overrides."""
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq("LLM_OVERRIDE"),
        )
        return {item["sk"]: item["instructions"] for item in resp.get("Items", [])}

    def save(self, toolset_name: str, instructions: str) -> None:
        _get_table().put_item(
            Item={
                "pk": "LLM_OVERRIDE",
                "sk": toolset_name,
                "instructions": instructions,
            }
        )

    def delete(self, toolset_name: str) -> None:
        _get_table().delete_item(Key={"pk": "LLM_OVERRIDE", "sk": toolset_name})


_llm_store = LLMInstructionsStore()


def get_llm_store() -> LLMInstructionsStore:
    return _llm_store


# ── Toolset enable/disable state store ─────────────────────────────────────────


class ToolsetStateStore:
    """Persist toolset enabled/disabled state across pod restarts."""

    def load_all(self) -> dict[str, bool]:
        """Return {toolset_name: enabled} for all stored state overrides."""
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq("TOOLSET_STATE"),
        )
        return {item["sk"]: item["enabled"] for item in resp.get("Items", [])}

    def save(self, toolset_name: str, enabled: bool) -> None:
        _get_table().put_item(
            Item={"pk": "TOOLSET_STATE", "sk": toolset_name, "enabled": enabled}
        )

    def delete(self, toolset_name: str) -> None:
        _get_table().delete_item(Key={"pk": "TOOLSET_STATE", "sk": toolset_name})


_toolset_state_store = ToolsetStateStore()


def get_toolset_state_store() -> ToolsetStateStore:
    return _toolset_state_store


# ── Webhook settings store ─────────────────────────────────────────────────────


class WebhookSettingsStore:
    """Persist per-webhook settings (e.g. write_back_enabled) across pod restarts."""

    def load_all(self) -> dict[str, dict]:
        """Return {webhook_id: {setting_key: value}} for all stored webhook settings."""
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq("WEBHOOK_SETTINGS"),
        )
        result: dict[str, dict] = {}
        for item in resp.get("Items", []):
            webhook_id = item["sk"]
            result[webhook_id] = {
                "write_back_enabled": item.get("write_back_enabled", True),
            }
        return result

    def get(self, webhook_id: str, project_id: Optional[str] = None) -> dict:
        """Return settings for a single webhook, with defaults if not stored.

        If *project_id* is given, check the project's ``webhook_write_back``
        overrides first.  A missing key or ``None`` value means "inherit global".
        """
        # ── Project-level override ────────────────────────────────────────
        if project_id:
            p = get_store().get(project_id)
            if p and p.webhook_write_back and webhook_id in p.webhook_write_back:
                val = p.webhook_write_back[webhook_id]
                if val is not None:
                    return {"write_back_enabled": val}

        # ── Global default ────────────────────────────────────────────────
        resp = _get_table().get_item(Key={"pk": "WEBHOOK_SETTINGS", "sk": webhook_id})
        item = resp.get("Item")
        if not item:
            return {"write_back_enabled": True}
        return {"write_back_enabled": item.get("write_back_enabled", True)}

    def save(self, webhook_id: str, write_back_enabled: bool) -> None:
        _get_table().put_item(
            Item={
                "pk": "WEBHOOK_SETTINGS",
                "sk": webhook_id,
                "write_back_enabled": write_back_enabled,
            }
        )


_webhook_settings_store = WebhookSettingsStore()


def get_webhook_settings_store() -> WebhookSettingsStore:
    return _webhook_settings_store


# ── App settings store ─────────────────────────────────────────────────────────


class AppSettingsStore:
    """Persist global application settings (e.g. webhook_dev_mode) across pod restarts."""

    _PK = "APP_SETTINGS"

    def get(self, key: str, default=None):
        """Return the stored value for *key*, or *default* if not found."""
        table_name = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
        if not table_name:
            return default
        try:
            resp = _get_table().get_item(Key={"pk": self._PK, "sk": key})
            item = resp.get("Item")
            if item is None:
                return default
            return item.get("value", default)
        except Exception:
            return default

    def set(self, key: str, value) -> None:
        """Persist *value* for *key*."""
        _get_table().put_item(Item={"pk": self._PK, "sk": key, "value": value})


_app_settings_store = AppSettingsStore()


def get_app_settings_store() -> AppSettingsStore:
    return _app_settings_store


# ── Investigation history store ────────────────────────────────────────────────


class ToolCallRecord(BaseModel):
    """One tool call made by Holmes during an investigation."""

    tool_name: str
    tool_input: dict = {}
    tool_output: str = ""
    # ISO timestamp when the tool was called
    called_at: str = ""


class Investigation(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    # When the investigation started (ISO 8601 UTC)
    started_at: str = ""
    # When the investigation finished (ISO 8601 UTC)
    finished_at: str = ""
    # "manual" | "webhook" | "cli"
    trigger: str = "manual"
    # Source system: "pagerduty" | "ado" | "salesforce" | "ui" | "cli"
    source: str = "ui"
    # ID of the incident/case/work-item in the source system (empty for ad-hoc UI chats)
    source_id: str = ""
    # URL to the source incident/case (empty if not applicable)
    source_url: str = ""
    # The user's question / prompt
    question: str = ""
    # Holmes's final answer
    answer: str = ""
    # Full tool call trace — every tool Holmes called, in order
    tool_calls: list[ToolCallRecord] = []
    # Project context (empty string = global / no project)
    project_id: str = ""
    # "running" | "completed" | "failed"
    status: str = "completed"
    # Error message if status == "failed"
    error: str = ""
    # User feedback: "helpful" | "not_helpful" | None (unrated)
    feedback: Optional[str] = None
    # User-curated resolution summary (only injected into LLM when feedback == "helpful")
    resolution_summary: Optional[str] = None
    # Analytics metadata: tokens, cost, model, duration (populated for new investigations)
    metadata: dict = {}


class InvestigationStore:
    """Persist investigation history to DynamoDB."""

    def save(self, investigation: Investigation) -> Investigation:
        """Insert or overwrite an investigation record."""
        _get_table().put_item(
            Item={
                "pk": f"INVESTIGATION#{investigation.id}",
                "sk": "META",
                "data": investigation.model_dump_json(),
                # Store started_at as a top-level attribute for efficient range queries
                "started_at": investigation.started_at,
                "source": investigation.source,
            }
        )
        return investigation

    def get(self, investigation_id: str) -> Optional[Investigation]:
        resp = _get_table().get_item(
            Key={"pk": f"INVESTIGATION#{investigation_id}", "sk": "META"}
        )
        item = resp.get("Item")
        if not item:
            return None
        return Investigation.model_validate_json(item["data"])

    def list(
        self,
        limit: int = 50,
        source: Optional[str] = None,
        project_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[Investigation]:
        """
        Return investigations sorted by started_at descending (newest first).

        Scans the table for INVESTIGATION# items. For small tables (< a few thousand
        investigations) a scan is acceptable; add a GSI on started_at if needed later.
        """
        from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

        filter_expr = Attr("pk").begins_with("INVESTIGATION#") & Attr("sk").eq("META")
        if source:
            filter_expr = filter_expr & Attr("source").eq(source)
        if start_date:
            filter_expr = filter_expr & Attr("started_at").gte(start_date)
        if end_date:
            filter_expr = filter_expr & Attr("started_at").lte(end_date)

        table = _get_table()
        items: list = []
        kwargs: dict = {"FilterExpression": filter_expr}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        investigations = [
            Investigation.model_validate_json(item["data"]) for item in items
        ]

        # Filter by project_id in Python (avoids another DynamoDB attribute)
        if project_id is not None:
            investigations = [i for i in investigations if i.project_id == project_id]

        # Sort newest first, cap at limit
        investigations.sort(key=lambda i: i.started_at, reverse=True)
        return investigations[:limit]

    def delete(self, investigation_id: str) -> bool:
        resp = _get_table().delete_item(
            Key={"pk": f"INVESTIGATION#{investigation_id}", "sk": "META"},
            ReturnValues="ALL_OLD",
        )
        return bool(resp.get("Attributes"))

    def update_feedback(
        self,
        investigation_id: str,
        feedback: str,
        resolution_summary: Optional[str] = None,
    ) -> Optional[Investigation]:
        """Update feedback and optional resolution summary on an investigation."""
        inv = self.get(investigation_id)
        if not inv:
            return None
        inv.feedback = feedback
        if resolution_summary is not None:
            inv.resolution_summary = resolution_summary
        return self.save(inv)

    def search_similar(
        self,
        query: str,
        project_id: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.1,
    ) -> list[dict]:
        """Find completed investigations similar to *query* using keyword overlap.

        Returns dicts with: id, question, answer_summary, source, started_at,
        score, tools_used, feedback, resolution_summary.

        Scoring:
        - Keyword overlap between query and investigation question+answer
        - Boost +0.15 for same project_id
        - Boost +0.10 for feedback == "helpful"
        - Recency decay: investigations older than 90 days get 0.8x multiplier
        """
        import math
        import re
        from datetime import datetime, timezone

        _STOPWORDS = frozenset(
            "the a an is are was were be been being have has had do does did "
            "will would shall should may might can could of in to for on with "
            "at by from as into through during before after above below between "
            "and or but not no nor so yet both either neither each every all "
            "any few more most other some such than too very it its this that "
            "these those i me my we our you your he him his she her they them "
            "their what which who whom how when where why".split()
        )

        def _tokenize(text: str) -> set[str]:
            words = re.findall(r"[a-z0-9]+", text.lower())
            return {w for w in words if w not in _STOPWORDS and len(w) > 1}

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # Load all completed investigations
        all_inv = self.list(limit=200)
        completed = [i for i in all_inv if i.status == "completed" and i.question]

        now = datetime.now(timezone.utc)
        results: list[dict] = []

        for inv in completed:
            inv_tokens = _tokenize(inv.question + " " + inv.answer[:1000])
            if not inv_tokens:
                continue

            # Jaccard-like overlap score
            overlap = query_tokens & inv_tokens
            if not overlap:
                continue
            score = len(overlap) / math.sqrt(len(query_tokens) * len(inv_tokens))

            # Boost for same project
            if project_id and inv.project_id == project_id:
                score += 0.15

            # Boost for user-approved investigations
            if inv.feedback == "helpful":
                score += 0.10

            # Recency decay: older than 90 days gets 0.8x
            try:
                inv_time = datetime.fromisoformat(inv.started_at.replace("Z", "+00:00"))
                age_days = (now - inv_time).days
                if age_days > 90:
                    score *= 0.8
            except (ValueError, AttributeError):
                pass

            if score < min_score:
                continue

            # Truncate answer for summary
            answer_summary = inv.resolution_summary or inv.answer[:500]
            if len(answer_summary) > 500:
                answer_summary = answer_summary[:497] + "..."

            results.append(
                {
                    "id": inv.id,
                    "question": inv.question,
                    "answer_summary": answer_summary,
                    "source": inv.source,
                    "started_at": inv.started_at,
                    "score": round(min(score, 1.0), 2),
                    "tools_used": list({tc.tool_name for tc in inv.tool_calls}),
                    "feedback": inv.feedback,
                    "resolution_summary": inv.resolution_summary,
                }
            )

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]


_investigation_store = InvestigationStore()


def get_investigation_store() -> InvestigationStore:
    return _investigation_store


# ── Per-project tool executor ──────────────────────────────────────────────────


def _fetch_secret(secret_arn: str) -> dict:
    """Fetch and JSON-decode a Secrets Manager secret."""
    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    resp = client.get_secret_value(SecretId=secret_arn)
    return json.loads(resp["SecretString"])


# MCP toolset types that can be scoped per-project via a per-project API key
_MCP_TOOLSET_TYPES = {"ado", "atlassian", "salesforce"}

# Default MCP server URLs (mirrors helm.tf configuration)
_MCP_DEFAULT_URLS = {
    "ado": "https://mcp-api.platform.pditechnologies.com/v1/ado-sse/mcp",
    "atlassian": "https://mcp-api.platform.pditechnologies.com/v1/atlassian-sse/mcp",
    "salesforce": "https://mcp-api.platform.pditechnologies.com/v1/salesforce-sse/mcp",
}

_MCP_ICONS = {
    "ado": "https://cdn.simpleicons.org/azuredevops/0078D7",
    "atlassian": "https://cdn.simpleicons.org/atlassian/0052CC",
    "salesforce": "https://cdn.simpleicons.org/salesforce/00A1E0",
}

_MCP_DESCRIPTIONS = {
    "ado": "Azure DevOps - work items, repositories, pipelines, and boards",
    "atlassian": "Atlassian - Jira issues, Confluence pages, and project boards",
    "salesforce": "Salesforce - accounts, contacts, opportunities, cases, and CRM data",
}

_MCP_INSTRUCTIONS_DIR = os.path.join(os.path.dirname(__file__), "mcp_instructions")


def _load_mcp_instructions(toolset_type: str) -> str:
    """Load LLM instructions for an MCP toolset from its Jinja2 template file."""
    path = os.path.join(_MCP_INSTRUCTIONS_DIR, f"{toolset_type}.jinja2")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logging.debug("No MCP instructions file found at %s", path)
        return ""
    except Exception:
        logging.exception("Failed to load MCP instructions from %s", path)
        return ""


def _build_mcp_toolset(instance: ToolsetInstance, api_key: str) -> object:
    """Dynamically instantiate a RemoteMCPToolset with a per-project API key."""
    from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset  # type: ignore

    url = instance.mcp_url or _MCP_DEFAULT_URLS.get(instance.type, "")
    if not url:
        raise ValueError(f"No URL configured for MCP toolset type '{instance.type}'")

    return RemoteMCPToolset(
        name=instance.name,
        description=_MCP_DESCRIPTIONS.get(instance.type, f"{instance.type} MCP server"),
        icon_url=_MCP_ICONS.get(instance.type),
        llm_instructions=_load_mcp_instructions(instance.type),
        config={
            "url": url,
            "mode": "streamable-http",
            "headers": {"x-api-key": api_key},
        },
    )


def _build_aws_toolset_with_account_filter(
    global_toolset: object, allowed_accounts: list[str]
) -> object:
    """
    Return a copy of the global AWS MCP toolset with LLM instructions restricted
    to only the allowed account profiles.

    We do this by cloning the toolset and overriding its llm_instructions to
    list only the permitted --profile values, so the LLM won't attempt to use
    accounts outside the project's scope.
    """
    import copy

    ts = copy.copy(global_toolset)

    # Build a filtered account description block
    account_lines = [
        "ALWAYS use --profile <account-name> in every AWS CLI command to target the correct account.",
        "NEVER run AWS commands without --profile.",
        "This project is scoped to the following AWS accounts only:",
        "",
    ]
    for acct in allowed_accounts:
        account_lines.append(f"  --profile {acct}")

    ts.llm_instructions = "\n".join(account_lines)
    return ts


def _instance_to_toolset_instance(instance: Instance) -> ToolsetInstance:
    """Convert a top-level Instance to a ToolsetInstance for tool loading."""
    return ToolsetInstance(
        type=instance.type,
        name=instance.name,
        secret_arn=instance.secret_arn,
        mcp_url=instance.mcp_url,
        aws_accounts=instance.aws_accounts,
    )


def build_project_tool_executor(
    project: Project,
    config,
    dal,
    instances_store: Optional[InstancesStore] = None,
):
    """
    Build a ToolExecutor scoped to the instances resolved for *project* via tag matching.

    If *instances_store* is provided, resolves instances by tag filter.
    Falls back to global tool executor if no instances are resolved.

    Handles three cases per instance:
    1. Python toolsets (grafana, prometheus) with secret_arn → dynamically instantiated
       with credentials from Secrets Manager.
    2. MCP toolsets (ado, atlassian, salesforce) with secret_arn → dynamically
       instantiated with per-project API key from Secrets Manager.
    3. AWS toolset (aws_api) with aws_accounts → reuses global toolset but overrides
       LLM instructions to restrict to the listed account profiles.
    4. Any instance whose name matches a global toolset and has no overrides → reused
       directly from the global executor.
    """
    from holmes.core.tools_utils.tool_executor import ToolExecutor  # type: ignore

    config.create_tool_executor(dal)  # ensure global executor is ready
    global_by_name = {ts.name: ts for ts in config._server_tool_executor.toolsets}

    # Resolve instances via tag filter
    toolset_instances: list[ToolsetInstance] = []
    if instances_store is not None:
        all_instances = instances_store.list()
        resolved = resolve_instances_for_project(project, all_instances)
        toolset_instances = [_instance_to_toolset_instance(i) for i in resolved]
    else:
        # No instances store provided — return empty executor (global tools only)
        toolset_instances = []

    project_toolsets = []
    for instance in toolset_instances:
        try:
            # ── MCP toolset with per-project API key ──────────────────────────
            if instance.type in _MCP_TOOLSET_TYPES:
                if instance.secret_arn:
                    creds = _fetch_secret(instance.secret_arn)
                    api_key = creds.get("api_key") or creds.get("x-api-key") or ""
                    if not api_key:
                        logging.warning(
                            "Secret %s for MCP toolset %s has no 'api_key' field — skipping",
                            instance.secret_arn,
                            instance.name,
                        )
                        continue
                else:
                    # No secret_arn — fall back to env var (e.g. MCP_ADO_API_KEY)
                    env_var = f"MCP_{instance.type.upper()}_API_KEY"
                    api_key = os.environ.get(env_var, "")
                    if not api_key:
                        logging.warning(
                            "MCP toolset %s has no secret_arn and env var %s is not set — skipping",
                            instance.name,
                            env_var,
                        )
                        continue
                ts = _build_mcp_toolset(instance, api_key)
                ts.check_prerequisites()
                project_toolsets.append(ts)
                continue

            # ── AWS toolset with account filter ───────────────────────────────
            if instance.type == "aws_api":
                if instance.aws_accounts:
                    global_ts = global_by_name.get(instance.name) or global_by_name.get(
                        "aws_api"
                    )
                    if global_ts is not None:
                        ts = _build_aws_toolset_with_account_filter(
                            global_ts, instance.aws_accounts
                        )
                        project_toolsets.append(ts)
                    else:
                        logging.warning(
                            "AWS toolset '%s' not found in global executor for project %s",
                            instance.name,
                            project.id,
                        )
                else:
                    # No account filter — reuse global aws_api toolset as-is
                    global_ts = global_by_name.get(instance.name) or global_by_name.get(
                        "aws_api"
                    )
                    if global_ts is not None:
                        project_toolsets.append(global_ts)
                    else:
                        logging.warning(
                            "Global aws_api toolset not found for project %s",
                            project.id,
                        )
                continue

            # ── Global toolset reuse (no per-project overrides) ───────────────
            # Match by instance name first, then by instance type (which IS the toolset name
            # for built-in toolsets like datadog/general, datadog/logs, grafana/dashboards, etc.)
            if instance.name in global_by_name:
                project_toolsets.append(global_by_name[instance.name])
                continue
            if instance.type in global_by_name:
                project_toolsets.append(global_by_name[instance.type])
                continue

            # ── Dynamically instantiate Python toolset with Secrets Manager creds ──
            creds = _fetch_secret(instance.secret_arn) if instance.secret_arn else {}
            synthetic_config = {instance.name: {"enabled": True, "config": creds}}
            from holmes.plugins.toolsets import (
                load_toolsets_from_config,  # type: ignore
            )

            new_toolsets = load_toolsets_from_config(
                synthetic_config, strict_check=False
            )
            if new_toolsets:
                ts = new_toolsets[0]
                ts.check_prerequisites()
                project_toolsets.append(ts)

        except Exception:
            logging.warning(
                "Failed to load toolset instance %s for project %s",
                instance.name,
                project.id,
                exc_info=True,
            )

    return ToolExecutor(project_toolsets)
