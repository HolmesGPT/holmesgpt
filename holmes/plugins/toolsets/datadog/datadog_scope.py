"""
Environment scoping for the Datadog toolsets.

The security boundary for restricting Holmes to a single environment is the
Datadog credential itself: a Datadog role carrying a restriction query
(e.g. ``env:staging``) issued to a service account. What lives in this module is
defence in depth — it is what Holmes can enforce on its own, and it is written
only where Datadog cannot restrict the data itself (notably metrics, which have
no row-level restriction mechanism).

Two enforcement styles are used, deliberately:

* **Injection** for the log and span search APIs, whose ``filter.query`` is a
  boolean search expression. The model's query is wrapped in parentheses and
  ANDed with the scope, so any ``OR`` the model wrote is contained.
* **Validation** for the metrics query API. Rewriting an LLM-authored metric
  query would need a tokenizer for Datadog's metric query language, and a parser
  bug there is a silent production leak. Validation is far easier to make
  correct, and it fails closed: anything this module cannot confidently prove is
  scoped is rejected.

When no scope is configured every helper here is inert and the toolsets behave
exactly as they did before.
"""

import re
from typing import Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Tag keys are conservative: Datadog tag keys are alphanumerics plus a few
# separators. Anything else is rejected at config time rather than escaped,
# because a scope value is concatenated into a Datadog query string.
_TAG_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-/]+$")

# Scope values deliberately exclude wildcards, whitespace, quotes, commas,
# parentheses and braces. A wildcard in a scope value would defeat the point of
# scoping, and the rest could break out of the query fragment we build.
_TAG_VALUE_RE = re.compile(r"^[A-Za-z0-9_.\-/:]+$")

# A single term inside a metric selector's `{...}` scope block, e.g. `env:staging`
# or `pod_name:web-*`. Values may contain wildcards here — only the scope tag
# itself has to match exactly, and every other term is just a further narrowing.
_SELECTOR_TERM_RE = re.compile(r"^[A-Za-z0-9_.\-/]+:[A-Za-z0-9_.\-/:*]*$")

# Used to decide whether a `{...}` block is a metric selector scope or the
# trailing `by {...}` grouping clause, which is not a scope and must not count
# towards the scope requirement.
_TRAILING_BY_RE = re.compile(r"(^|[^A-Za-z0-9_])by$", re.IGNORECASE)


class DatadogScopeConfig(BaseModel):
    """Restricts every Datadog toolset to data carrying the given tags."""

    model_config = ConfigDict(extra="forbid")

    tags: Dict[str, Union[str, List[str]]] = Field(
        title="Scope Tags",
        description=(
            "Tags that all Datadog data reachable by Holmes must carry, e.g. "
            "{'env': 'staging'}. Values are matched exactly — 'staging' does not "
            "match 'staging-eu'. A list of values means any one of them. This is "
            "defence in depth: the real boundary is a Datadog role restriction "
            "query on the credential Holmes uses."
        ),
        examples=[{"env": "staging"}, {"env": ["staging", "dev"]}],
    )

    @field_validator("tags")
    @classmethod
    def _validate_tags(
        cls, tags: Dict[str, Union[str, List[str]]]
    ) -> Dict[str, Union[str, List[str]]]:
        if not tags:
            raise ValueError(
                "datadog scope.tags must not be empty. Either remove the 'scope' "
                "block entirely (Holmes then behaves as if unscoped) or specify at "
                "least one tag, e.g. scope: {tags: {env: staging}}"
            )

        for key, value in tags.items():
            if not _TAG_KEY_RE.match(key):
                raise ValueError(
                    f"datadog scope tag key {key!r} is invalid. Tag keys may only "
                    "contain letters, digits, and the characters _ . - /"
                )
            values = value if isinstance(value, list) else [value]
            if not values:
                raise ValueError(
                    f"datadog scope tag {key!r} has an empty list of values. Give it "
                    "at least one value, or remove it."
                )
            for item in values:
                if not isinstance(item, str) or not _TAG_VALUE_RE.match(item):
                    raise ValueError(
                        f"datadog scope tag value {item!r} for key {key!r} is invalid. "
                        "Values may only contain letters, digits, and the characters "
                        "_ . - / : — in particular wildcards, whitespace and quotes "
                        "are rejected, because a wildcard would defeat the scope and "
                        "the rest could alter the generated Datadog query."
                    )
        return tags

    def values_for(self, key: str) -> List[str]:
        """Normalise a tag's configured value(s) to a list."""
        value = self.tags[key]
        return list(value) if isinstance(value, list) else [value]

    def describe(self) -> str:
        """Human-readable rendering of the scope, used in error messages."""
        parts = []
        for key in self.tags:
            values = self.values_for(key)
            if len(values) == 1:
                parts.append(f"{key}:{values[0]}")
            else:
                parts.append(f"{key}:({' or '.join(values)})")
        return ", ".join(parts)


def build_scope_query(scope: DatadogScopeConfig) -> str:
    """
    Render the scope as a Datadog search-query fragment.

    Every clause is parenthesised so the result can be safely ANDed with a query
    written by the model.
    """
    clauses = []
    for key in scope.tags:
        values = scope.values_for(key)
        if len(values) == 1:
            clauses.append(f"{key}:{values[0]}")
        else:
            clauses.append("(" + " OR ".join(f"{key}:{v}" for v in values) + ")")
    if len(clauses) == 1:
        return clauses[0]
    return " AND ".join(f"({c})" if not c.startswith("(") else c for c in clauses)


def apply_scope_to_search_query(
    scope: Optional[DatadogScopeConfig], query: Optional[str]
) -> str:
    """
    Combine a model-authored log/span search query with the configured scope.

    The model's query is parenthesised before being ANDed, which is what makes
    this safe: it contains any ``OR`` the model wrote, so ``a OR b`` becomes
    ``(a OR b) AND (env:staging)`` rather than ``a OR b AND env:staging``.

    With no scope configured the query is returned unchanged, so existing
    behaviour is preserved byte for byte.
    """
    normalised = (query or "").strip()
    if scope is None:
        return normalised or "*"

    scope_query = build_scope_query(scope)
    if not normalised or normalised == "*":
        return scope_query
    return f"({normalised}) AND ({scope_query})"


def build_metrics_tag_filter(scope: DatadogScopeConfig) -> str:
    """
    Render the scope for the ``tag_filter`` parameter of GET /api/v1/metrics.

    That endpoint takes a single ``tag:value`` filter rather than a boolean
    expression, so only the first value of the first tag can be expressed.
    """
    key = next(iter(scope.tags))
    return f"{key}:{scope.values_for(key)[0]}"


def _split_selector_terms(block: str) -> List[str]:
    """Split a metric selector scope block on commas, dropping empty terms."""
    return [term.strip() for term in block.split(",") if term.strip()]


def _find_selector_blocks(query: str) -> Tuple[List[str], Optional[str]]:
    """
    Extract the scope blocks of every metric selector in a metric query.

    Returns ``(blocks, error)``. ``error`` is set when the query cannot be parsed
    with confidence, in which case ``blocks`` is meaningless and the caller must
    reject the query. Braces do not nest in Datadog's metric query language, so
    a nested ``{`` is treated as unparseable rather than guessed at.

    The trailing ``by {...}`` grouping clause is not a scope and is skipped.
    """
    blocks: List[str] = []
    index = 0
    while index < len(query):
        char = query[index]
        if char == "}":
            return [], "unbalanced '}' in the query"
        if char != "{":
            index += 1
            continue

        end = query.find("}", index + 1)
        if end == -1:
            return [], "unbalanced '{' in the query"
        block = query[index + 1 : end]
        if "{" in block:
            return [], "nested '{' in the query"

        # `by {host}` is a grouping clause, not a selector scope.
        preceding = query[:index].rstrip()
        if not _TRAILING_BY_RE.search(preceding):
            blocks.append(block)
        index = end + 1

    return blocks, None


def validate_metric_query(
    scope: Optional[DatadogScopeConfig], query: str
) -> Optional[str]:
    """
    Check that every metric selector in ``query`` is restricted to the scope.

    Returns ``None`` when the query is acceptable, or an actionable error message
    naming the offending selector when it is not. Returns ``None`` immediately
    when no scope is configured.

    This validates and never rewrites, and it fails closed: a query it cannot
    parse is rejected rather than passed through.
    """
    if scope is None:
        return None

    described = scope.describe()
    stripped = (query or "").strip()
    if not stripped:
        return (
            f"The metric query is empty. Holmes is restricted to {described}, so every "
            "metric selector must be scoped, e.g. "
            f"system.cpu.user{{{build_metrics_tag_filter(scope)}}}"
        )

    blocks, parse_error = _find_selector_blocks(stripped)
    if parse_error:
        return (
            f"The metric query could not be parsed ({parse_error}), so Holmes cannot "
            f"verify it is restricted to {described}. Rewrite it as a plain metric "
            f"query where each selector's scope is a comma-separated list of tags, "
            f"e.g. avg:system.cpu.user{{{build_metrics_tag_filter(scope)}}}. "
            f"Query: {stripped}"
        )

    if not blocks:
        return (
            f"The metric query has no metric selector scope, so it would read data "
            f"outside the environment Holmes is restricted to ({described}). Add a "
            f"scope to every metric, e.g. "
            f"avg:system.cpu.user{{{build_metrics_tag_filter(scope)}}}. "
            f"Query: {stripped}"
        )

    for block in blocks:
        error = _validate_selector_block(scope, block, described)
        if error:
            return f"{error} Query: {stripped}"

    return None


def _validate_selector_block(
    scope: DatadogScopeConfig, block: str, described: str
) -> Optional[str]:
    """Validate one metric selector's `{...}` scope against the configured scope."""
    rendered = f"{{{block}}}"
    terms = _split_selector_terms(block)

    if not terms:
        return (
            f"The metric selector scope {rendered} is empty, so it matches every "
            f"environment. Holmes is restricted to {described}; scope it with "
            f"{{{build_metrics_tag_filter(scope)}}}."
        )

    # Fail closed on anything that is not a plain `key:value` term. Boolean
    # operators, negation and `IN (...)` lists all land here: a term such as
    # `env:staging OR env:prod` contains the scope tag as a substring but is not
    # restricted by it, so substring matching would be unsafe.
    for term in terms:
        if term == "*":
            return (
                f"The metric selector scope {rendered} matches every environment "
                f"because of the '*' term. Holmes is restricted to {described}; "
                f"replace '*' with {build_metrics_tag_filter(scope)}."
            )
        if not _SELECTOR_TERM_RE.match(term):
            return (
                f"The metric selector scope {rendered} contains the term {term!r}, "
                "which is not a plain tag:value filter. Under environment scoping "
                "Holmes only accepts selectors made of comma-separated tag:value "
                "terms — boolean operators (OR/AND/NOT), negation and IN(...) lists "
                f"are rejected because they can widen the scope. Holmes is "
                f"restricted to {described}; use "
                f"{{{build_metrics_tag_filter(scope)}}} and narrow further with "
                "additional comma-separated tags."
            )

    # Every configured tag must be pinned to one of its allowed values. Matching
    # is case-insensitive because Datadog normalises tags to lower case.
    lowered = {term.lower() for term in terms}
    for key in scope.tags:
        allowed = {f"{key}:{value}".lower() for value in scope.values_for(key)}
        if not (lowered & allowed):
            expected = " or ".join(sorted(f"{key}:{v}" for v in scope.values_for(key)))
            return (
                f"The metric selector scope {rendered} is missing the required "
                f"{key} tag. Holmes is restricted to {described}, so every selector "
                f"must include {expected} as one of its comma-separated terms."
            )

    return None


def no_data_suffix(scope: Optional[DatadogScopeConfig]) -> str:
    """
    Explanatory sentence appended to empty results when a scope is configured.

    Without this the model reads an empty result as "the service is healthy",
    when the truth may be "that data exists but is outside the scope".
    """
    if scope is None:
        return ""
    return (
        f"\nNote: Holmes is restricted to {scope.describe()}. This search covered "
        "only that scope, so data outside it is not visible and its absence here "
        "does not mean it does not exist."
    )
