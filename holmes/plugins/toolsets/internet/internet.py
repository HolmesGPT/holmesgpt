import logging
import os
import re
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple, Type
from urllib.parse import urljoin, urlparse

from pydantic import Field
import requests  # type: ignore
from bs4 import BeautifulSoup
from markdownify import markdownify
from requests import RequestException, Timeout  # type: ignore

from holmes.core.tools import (
    ApprovalRequirement,
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.internet.ssrf import (
    ALLOWED_SCHEMES,
    SSRFValidationError,
    build_pinned_adapter,
    validate_url,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

# Headers that carry credentials and must never survive a cross-host redirect.
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})

# Bound the manual (validated) redirect chain, mirroring requests' default.
MAX_REDIRECTS = 5

# TODO: change and make it holmes
INTERNET_TOOLSET_USER_AGENT = os.environ.get(
    "INTERNET_TOOLSET_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0; holmesgpt;) Gecko/20100101 Firefox/128.0",
)
INTERNET_TOOLSET_TIMEOUT_SECONDS = int(
    os.environ.get("INTERNET_TOOLSET_TIMEOUT_SECONDS", "5")
)

SELECTORS_TO_REMOVE = [
    "script",
    "style",
    "link",
    "noscript",
    "header",
    "footer",
    "nav",
    "iframe",
    "svg",
    "img",
    "button",
    "menu",
    "sidebar",
    "aside",
    ".header",
    ".footer",
    ".navigation",
    ".nav",
    ".menu",
    ".sidebar",
    ".ad",
    ".advertisement",
    ".social",
    ".popup",
    ".modal",
    ".banner",
    ".cookie-notice",
    ".social-share",
    ".related-articles",
    ".recommended",
    "#header",
    "#footer",
    "#navigation",
    "#nav",
    "#menu",
    "#sidebar",
    "#ad",
    "#advertisement",
    "#social",
    "#popup",
    "#modal",
    "#banner",
    "#cookie-notice",
    "#social-share",
    "#related-articles",
    "#recommended",
]


def _strip_sensitive_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Drop credential-bearing headers (used when following a cross-host
    redirect so operator-configured auth is never leaked to another host)."""
    return {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_HEADERS}


def scrape(
    url: str,
    headers: Dict[str, str],
    allowed_hosts: Optional[Sequence[str]] = None,
    block_internal_ips: bool = True,
    follow_cross_host_redirects: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """Fetch ``url`` with SSRF protection.

    Every hop (including redirects) is validated against the SSRF policy and the
    connection is pinned to the exact IP that was validated, defeating DNS
    rebinding. Credential headers are stripped when a redirect crosses to a
    different host.
    """
    content = None
    mime_type = None
    if not headers:
        headers = {}
    headers = dict(headers)
    headers["User-Agent"] = INTERNET_TOOLSET_USER_AGENT

    current_url = url
    current_host = (urlparse(url).hostname or "").lower()
    current_headers = headers

    try:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                validated_ips = validate_url(
                    current_url,
                    allowed_hosts=allowed_hosts,
                    block_internal_ips=block_internal_ips,
                )
            except SSRFValidationError as e:
                error_message = f"Refusing to fetch {current_url}: {e}"
                logging.warning(error_message)
                return error_message, None

            session = requests.Session()
            adapter = build_pinned_adapter(validated_ips[0])
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            try:
                response = session.get(
                    current_url,
                    headers=current_headers,
                    timeout=INTERNET_TOOLSET_TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
            finally:
                session.close()

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    break
                current_url = urljoin(current_url, location)
                next_host = (urlparse(current_url).hostname or "").lower()
                if next_host != current_host:
                    # www.<host> is the same site the user approved.
                    same_site = next_host.removeprefix("www.") == current_host.removeprefix("www.")
                    if not follow_cross_host_redirects and not same_site:
                        error_message = (
                            f"Refusing to follow the redirect from {current_host} to "
                            f"{current_url}: only {current_host} was approved. Fetch "
                            "that URL directly to ask for approval of its host."
                        )
                        logging.warning(error_message)
                        return error_message, None
                    current_headers = _strip_sensitive_headers(current_headers)
                    current_host = next_host
                continue

            response.raise_for_status()
            break
        else:
            error_message = f"Failed to load {url}: exceeded {MAX_REDIRECTS} redirects"
            logging.warning(error_message)
            return error_message, None
    except Timeout:
        error_message = f"Failed to load {url}. Timeout after {INTERNET_TOOLSET_TIMEOUT_SECONDS} seconds"
        logging.error(
            error_message,
            exc_info=True,
        )
        return error_message, None
    except RequestException as e:
        error_message = f"Failed to load {url}: {str(e)}"
        logging.warning(error_message, exc_info=True)
        return error_message, None

    if response:
        content = response.text
        try:
            content_type = response.headers["content-type"]
            if content_type:
                mime_type = content_type.split(";")[0]
        except Exception:
            logging.info(
                f"Failed to parse content type from headers {response.headers}"
            )

    return (content, mime_type)


def cleanup(soup: BeautifulSoup):
    """Remove all elements that are irrelevant to the textual representation of a web page.
    This includes images, extra data, even links as there is no intention to navigate from that page.
    """

    for selector in SELECTORS_TO_REMOVE:
        for element in soup.select(selector):
            element.decompose()

    for tag in soup.find_all(True):
        for attr in list(tag.attrs):  # type: ignore
            if attr != "href":
                tag.attrs.pop(attr, None)  # type: ignore

    return soup


def html_to_markdown(page_source: str):
    soup = BeautifulSoup(page_source, "html.parser")
    soup = cleanup(soup)
    page_source = str(soup)

    try:
        md = markdownify(page_source)
    except OSError as e:
        logging.error(
            f"There was an error in converting the HTML to markdown. Falling back to returning the raw HTML. Error: {str(e)}"
        )
        return page_source

    md = re.sub(r"</div>", "      ", md)
    md = re.sub(r"<div>", "     ", md)

    md = re.sub(r"\n\s*\n", "\n\n", md)

    return md


def looks_like_html(content):
    """
    Check if the content looks like HTML.
    """
    if isinstance(content, str):
        # Check for common HTML tags
        html_patterns = [r"<!DOCTYPE\s+html", r"<html", r"<head", r"<body"]
        return any(
            re.search(pattern, content, re.IGNORECASE) for pattern in html_patterns
        )
    return False


class FetchWebpage(Tool):
    toolset: "InternetToolset"

    def __init__(self, toolset: "InternetToolset"):
        super().__init__(
            name="fetch_webpage",
            description="Fetch a webpage. Use this to fetch skills if they are present before starting your investigation (if no other tool like confluence is more appropriate). Internet access is off by default: a host outside the operator's allowlist needs the user's approval, and is refused where no approval is possible.",
            parameters={
                "url": ToolParameter(
                    description="The URL to fetch",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def requires_approval(
        self, params: Dict, context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        config = self.toolset.internet_config or InternetToolsetConfig()
        # With an allowlist, scrape() refuses every other host; nothing to approve.
        if not config.require_approval or config.allowed_hosts:
            return None
        try:
            parsed = urlparse(str(params.get("url") or ""))
            host = parsed.hostname
        except ValueError:
            return None
        if parsed.scheme.lower() not in ALLOWED_SCHEMES or not host:
            return None
        return ApprovalRequirement(
            needs_approval=True,
            reason=(
                f"Fetching a web page from '{host}' needs approval: internet access is off "
                "by default and this host is not in the internet toolset's allowed_hosts."
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        url = params.get("url")
        if not url or not isinstance(url, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'url' parameter is required and was not provided.",
                params=params,
            )

        additional_headers = (
            self.toolset.internet_config.additional_headers if self.toolset.internet_config.additional_headers else {}
        )
        allowed_hosts = self.toolset.internet_config.allowed_hosts
        # Only forward operator-configured auth headers to explicitly
        # allowlisted hosts. Without an allowlist the model can point the tool
        # at an arbitrary host, so the headers (which may carry credentials)
        # must not be sent.
        if additional_headers and not allowed_hosts:
            logging.warning(
                "Not sending configured additional_headers for %s: set "
                "'allowed_hosts' to forward headers to specific hosts.",
                url,
            )
        if not allowed_hosts:
            additional_headers = {}
        content, mime_type = scrape(
            url,
            additional_headers,
            allowed_hosts=allowed_hosts,
            block_internal_ips=self.toolset.internet_config.block_internal_ips,
            # An approval covers the host the user saw, not where it redirects.
            follow_cross_host_redirects=bool(allowed_hosts)
            or not self.toolset.internet_config.require_approval,
        )

        if not content:
            logging.error(f"Failed to retrieve content from {url}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to retrieve content from {url}",
                params=params,
            )

        # Check if the content is HTML based on MIME type or content
        if (mime_type and mime_type.startswith("text/html")) or (
            mime_type is None and looks_like_html(content)
        ):
            content = html_to_markdown(content)

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=content,
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        url: str = params.get("url", "<missing url>")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Webpage {url}"


class InternetBaseToolsetConfig(ToolsetConfig):
    additional_headers: Dict[str, str] = Field(
        default_factory=dict,
        title="Headers",
        description="Additional HTTP headers to include in requests",
        examples=[
            {},
            {"Authorization": "Basic <base64_encoded_credentials>"},
            {"Authorization": "Bearer <token>"},
        ],
    )
    allowed_hosts: List[str] = Field(
        default_factory=list,
        title="Allowed hosts",
        description=(
            "Optional allowlist of hostnames the toolset may fetch. When set, "
            "only these hosts (and their subdomains) can be fetched, and they "
            "are exempt from the internal-IP block so operators can point the "
            "tool at a known internal endpoint on purpose. Auth headers are "
            "only sent to allowlisted hosts. When empty, any public host may be "
            "fetched but internal/non-routable addresses are blocked."
        ),
        examples=[[], ["docs.example.com"], ["wiki.internal.corp"]],
    )
    block_internal_ips: bool = Field(
        default=True,
        title="Block internal IPs",
        description=(
            "Reject fetches whose host resolves to a loopback, link-local, "
            "private, reserved, multicast or unspecified address (SSRF "
            "protection). Only disable in trusted, isolated environments."
        ),
    )


class InternetToolsetConfig(InternetBaseToolsetConfig):
    require_approval: bool = Field(
        default=True,
        title="Require approval",
        description=(
            "When 'allowed_hosts' is empty, ask the user before each fetch and "
            "refuse it where no approval is possible (alert investigations, "
            "scheduled runs, API calls without tool approval). Set to false to "
            "let Holmes fetch any public host without asking."
        ),
    )


class InternetBaseToolset(Toolset):
    config_classes: ClassVar[list[Type[InternetBaseToolsetConfig]]] = [
        InternetBaseToolsetConfig
    ]
    
    internet_config: Optional[InternetBaseToolsetConfig] = None

    def __init__(
        self,
        name: str,
        description: str,
        icon_url: str,
        tools: list[Tool],
        tags: List[ToolsetTag],
        docs_url: Optional[str] = None,
        **kwargs: Any,
    ):
        super().__init__(
            name=name,
            description=description,
            icon_url=icon_url,
            prerequisites=[
                CallablePrerequisite(callable=self.prerequisites_callable),
            ],
            tools=tools,
            tags=tags,
            docs_url=docs_url,
            **kwargs,
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.internet_config = self.config_classes[0](**(config or {}))
        except Exception as e:
            return False, f"Invalid {self.name} configuration: {e}"
        return True, ""


class InternetToolset(InternetBaseToolset):
    config_classes: ClassVar[list[Type[InternetBaseToolsetConfig]]] = [
        InternetToolsetConfig
    ]

    internet_config: Optional[InternetToolsetConfig] = None

    def __init__(self):
        super().__init__(
            name="internet",
            description="Fetch webpages",
            icon_url="https://platform.robusta.dev/demos/internet-access.svg",
            tools=[
                FetchWebpage(self),
            ],
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/internet/",
            tags=[
                ToolsetTag.CORE,
            ],
            enabled=True,
        )
