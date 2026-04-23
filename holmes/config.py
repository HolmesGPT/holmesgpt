import logging
import os
import os.path
import threading
from enum import Enum
from pathlib import Path

display_logger = logging.getLogger("holmes.display.config")
from typing import TYPE_CHECKING, Any, List, Optional, Union

import sentry_sdk
import yaml  # type: ignore
from pydantic import (
    BaseModel,
    ConfigDict,
    FilePath,
    PrivateAttr,
    SecretStr,
)

from holmes.core.init_event import EventCallback, StatusEvent, StatusEventKind
from holmes.core.llm import DefaultLLM, LLMModelRegistry
from holmes.core.tools import PrerequisiteCacheMode, Toolset, ToolsetTag
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.toolset_manager import ToolsetManager
from holmes.core.transformers.llm_summarize import LLMSummarizeTransformer
from holmes.plugins.runbooks import (
    RunbookCatalog,
    load_runbook_catalog,
)

# Source plugin imports moved to their respective create methods to speed up startup
if TYPE_CHECKING:
    from holmes.core.tool_calling_llm import ToolCallingLLM
    from holmes.plugins.destinations.slack import SlackDestination
    from holmes.plugins.sources.github import GitHubSource
    from holmes.plugins.sources.jira import JiraServiceManagementSource, JiraSource
    from holmes.plugins.sources.opsgenie import OpsGenieSource
    from holmes.plugins.sources.pagerduty import PagerDutySource
    from holmes.plugins.sources.prometheus.plugin import AlertManagerSource

from holmes.core.config import config_path_dir
from holmes.core.oauth_utils import eager_load_oauth_tools, preload_oauth_tokens, set_oauth_dal
from holmes.core.supabase_dal import SupabaseDal
from holmes.utils.definitions import RobustaConfig
from holmes.utils.pydantic_utils import RobustaBaseConfig, load_model_from_file



DEFAULT_CONFIG_LOCATION = os.path.join(config_path_dir, "config.yaml")


class SupportedTicketSources(str, Enum):
    JIRA_SERVICE_MANAGEMENT = "jira-service-management"
    PAGERDUTY = "pagerduty"


class Config(RobustaBaseConfig):
    model: Optional[str] = None
    _model_source: Optional[str] = None  # tracks where the model was set from
    api_key: Optional[SecretStr] = (
        None  # if None, read from OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT env var
    )
    api_base: Optional[str] = None
    api_version: Optional[str] = None
    fast_model: Optional[str] = None
    max_steps: int = 100
    cluster_name: Optional[str] = None

    alertmanager_url: Optional[str] = None
    alertmanager_username: Optional[str] = None
    alertmanager_password: Optional[str] = None
    alertmanager_alertname: Optional[str] = None
    alertmanager_label: Optional[List[str]] = []
    alertmanager_file: Optional[FilePath] = None

    jira_url: Optional[str] = None
    jira_username: Optional[str] = None
    jira_api_key: Optional[SecretStr] = None
    jira_query: Optional[str] = ""

    github_url: Optional[str] = None
    github_owner: Optional[str] = None
    github_pat: Optional[SecretStr] = None
    github_repository: Optional[str] = None
    github_query: str = ""

    slack_token: Optional[SecretStr] = None
    slack_channel: Optional[str] = None

    pagerduty_api_key: Optional[SecretStr] = None
    pagerduty_user_email: Optional[str] = None
    pagerduty_incident_key: Optional[str] = None

    opsgenie_api_key: Optional[SecretStr] = None
    opsgenie_team_integration_key: Optional[SecretStr] = None
    opsgenie_query: Optional[str] = None

    custom_runbook_catalogs: List[Union[str, FilePath]] = []

    # custom_toolsets is passed from config file, and be used to override built-in toolsets, provides 'stable' customized toolset.
    # The status of custom toolsets can be cached.
    custom_toolsets: Optional[List[FilePath]] = None
    # custom_toolsets_from_cli is passed from CLI option `--custom-toolsets` as 'experimental' custom toolsets.
    # The status of toolset here won't be cached, so the toolset from cli will always be loaded when specified in the CLI.
    custom_toolsets_from_cli: Optional[List[FilePath]] = None
    # if True, we will try to load the Robusta AI model, in cli we aren't trying to load it.
    should_try_robusta_ai: bool = False

    # Ignored by Holmes - exists solely to allow YAML anchors/aliases in config files.
    # Define reusable blocks here and reference them elsewhere with YAML aliases (*).
    anchors: Optional[Any] = None

    toolsets: Optional[dict[str, dict[str, Any]]] = None
    mcp_servers: Optional[dict[str, dict[str, Any]]] = None
    additional_toolsets: Optional[List[Toolset]] = None

    # Thread-safe executor cache: stores (executor, cache_key) where cache_key
    # is (tuple(tags), enable_all_toolsets_possible) so callers with different
    # parameters don't silently receive a stale executor.
    _cached_tool_executor: Optional[ToolExecutor] = None
    _cached_executor_key: Optional[tuple] = None
    _executor_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    # TODO: Separate those fields to facade class, this shouldn't be part of the config.
    _toolset_manager: Optional[ToolsetManager] = PrivateAttr(None)
    _llm_model_registry: Optional[LLMModelRegistry] = PrivateAttr(None)
    _dal: Optional[SupabaseDal] = PrivateAttr(None)
    _config_file_path: Optional[Path] = PrivateAttr(None)

    @property
    def cached_tool_executor(self) -> Optional[ToolExecutor]:
        """Thread-safe read access to the cached executor."""
        with self._executor_lock:
            return self._cached_tool_executor

    @property
    def toolset_manager(self) -> ToolsetManager:
        if not self._toolset_manager:
            # Set the class-level default once before any transformers are
            # instantiated.  ToolsetManager no longer needs to know about it.
            if self.fast_model:
                LLMSummarizeTransformer.set_default_fast_model(self.fast_model)

            self._toolset_manager = ToolsetManager(
                toolsets=self.toolsets,
                mcp_servers=self.mcp_servers,
                custom_toolsets=self.custom_toolsets,
                custom_toolsets_from_cli=self.custom_toolsets_from_cli,
                custom_runbook_catalogs=self.custom_runbook_catalogs,
                config_file_path=self._config_file_path,
                additional_toolsets=self.additional_toolsets,
            )
        return self._toolset_manager

    @property
    def dal(self) -> SupabaseDal:
        if not self._dal:
            self._dal = SupabaseDal(self.cluster_name)  # type: ignore
        return self._dal

    @property
    def llm_model_registry(self) -> LLMModelRegistry:
        if not self._llm_model_registry:
            self._llm_model_registry = LLMModelRegistry(self, dal=self.dal)
        return self._llm_model_registry



    def log_useful_info(self):
        if self.llm_model_registry.models:
            display_logger.info(
                f"Loaded models: {list(self.llm_model_registry.models.keys())}"
            )
        else:
            display_logger.warning("No llm models were loaded")

    @classmethod
    def load_from_file(cls, config_file: Optional[Path], **kwargs) -> "Config":
        """
        Load configuration from file and merge with CLI options.

        Args:
            config_file: Path to configuration file
            **kwargs: CLI options to override config file values

        Returns:
            Config instance with merged settings
        """

        config_from_file: Optional[Config] = None
        if config_file is not None and config_file.exists():
            logging.debug(f"Loading config from {config_file}")
            config_from_file = load_model_from_file(cls, config_file)

        cli_options = {k: v for k, v in kwargs.items() if v is not None and v != []}

        if config_from_file is None:
            result = cls(**cli_options)
        else:
            logging.debug(f"Overriding config from cli options {cli_options}")
            merged_config = config_from_file.dict()
            merged_config.update(cli_options)
            result = cls(**merged_config)

        if config_file is not None and config_file.exists():
            result._config_file_path = config_file

        # Track where the model setting came from
        if "model" in cli_options:
            pass  # CLI --model flag: no source label needed (user just typed it)
        elif config_from_file is not None and config_from_file.model is not None:
            result._model_source = f"in {config_file}"
        # Fall through to env var check below

        if result.model is None:
            model_from_env = os.environ.get("MODEL")
            if model_from_env and model_from_env.strip():
                result.model = model_from_env
                result._model_source = "via $MODEL"

        result.log_useful_info()
        return result

    @classmethod
    def load_from_env(cls):
        kwargs = {}
        for field_name in [
            "model",
            "fast_model",
            "api_key",
            "api_base",
            "api_version",
            "max_steps",
            "alertmanager_url",
            "alertmanager_username",
            "alertmanager_password",
            "jira_url",
            "jira_username",
            "jira_api_key",
            "jira_query",
            "slack_token",
            "slack_channel",
            "github_url",
            "github_owner",
            "github_repository",
            "github_pat",
            "github_query",
        ]:
            val = os.getenv(field_name.upper(), None)
            if val is not None:
                kwargs[field_name] = val
        kwargs["cluster_name"] = Config.__get_cluster_name()
        kwargs["should_try_robusta_ai"] = True
        result = cls(**kwargs)
        if "model" in kwargs:
            result._model_source = "via $MODEL"
        result.log_useful_info()
        return result

    @staticmethod
    def get_robusta_global_config_value(key: str) -> Optional[str]:
        """Read a value from Robusta's global_config. Returns None in CLI mode or on error."""
        from holmes.common.env_vars import ROBUSTA_CONFIG_PATH

        if not os.path.exists(ROBUSTA_CONFIG_PATH):
            return None
        try:
            with open(ROBUSTA_CONFIG_PATH) as f:
                yaml_content = yaml.safe_load(f)
                config = RobustaConfig(**yaml_content)
                return config.global_config.get(key)
        except Exception:
            logging.warning("Failed to load '%s' from Robusta config", key, exc_info=True)
            return None

    @staticmethod
    def __get_cluster_name() -> Optional[str]:
        env_cluster_name = os.environ.get("CLUSTER_NAME")
        if env_cluster_name:
            return env_cluster_name
        return Config.get_robusta_global_config_value("cluster_name")

    def get_runbook_catalog(self) -> Optional[RunbookCatalog]:
        runbook_catalog = load_runbook_catalog(
            dal=self.dal, custom_catalog_paths=self.custom_runbook_catalogs
        )
        return runbook_catalog

    # ── Unified factory methods ──

    @staticmethod
    def _executor_cache_key(
        tags: List[ToolsetTag], enable_all: bool
    ) -> tuple:
        return (tuple(sorted(tags, key=lambda t: t.value)), enable_all)

    def create_tool_executor(
        self,
        dal: Optional["SupabaseDal"] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
        enable_all_toolsets_possible: bool = True,
        prerequisite_cache: PrerequisiteCacheMode = PrerequisiteCacheMode.ENABLED,
        reuse_executor: bool = False,
        on_event: EventCallback = None,
    ) -> ToolExecutor:
        """
        Create a ToolExecutor with explicit behavioral controls.

        Args:
            dal: Optional database access layer.
            toolset_tag_filter: Only include toolsets whose tags overlap with this
                list (e.g. ``[ToolsetTag.CORE, ToolsetTag.CLI]``). Toolsets that
                don't match any tag are excluded entirely — they won't be loaded,
                checked, or returned. This filter is independent of
                ``enable_all_toolsets_possible``: a toolset must pass the tag filter first, then
                ``enable_all_toolsets_possible`` controls whether it gets enabled automatically.
                Defaults to ``[ToolsetTag.CORE]`` if not specified.
            enable_all_toolsets_possible: If True, automatically enable every toolset (that
                passed the tag filter) that can work without explicit configuration.
                If False, only toolsets explicitly enabled in config are loaded.
            prerequisite_cache: Controls prerequisite check caching behavior.
                DISABLED — run full checks eagerly, no disk caching.
                ENABLED — use cached results when available (default).
                FORCE_REFRESH — re-run all checks and update the cache.
            reuse_executor: If True, cache the executor in memory and return the same
                instance on subsequent calls with the *same* parameters.
                A call with different ``toolset_tag_filter`` or
                ``enable_all_toolsets_possible`` will create and cache a fresh executor.

        Migration from removed helpers
        ------------------------------
        ``create_console_tool_executor(dal, refresh_status)``::

            create_tool_executor(
                dal=dal,
                toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
                enable_all_toolsets_possible=True,
                prerequisite_cache=PrerequisiteCacheMode.FORCE_REFRESH if refresh_status else PrerequisiteCacheMode.ENABLED,
            )

        ``create_agui_tool_executor(dal)``::

            create_tool_executor(
                dal=dal,
                toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
                enable_all_toolsets_possible=True,
                prerequisite_cache=PrerequisiteCacheMode.FORCE_REFRESH,
                reuse_executor=True,
            )

        ``refresh_server_tool_executor(dal)``::

            refresh_tool_executor(
                dal=dal,
                toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
                enable_all_toolsets_possible=False,
            )
        """
        tags = toolset_tag_filter or [ToolsetTag.CORE]
        cache_key = self._executor_cache_key(tags, enable_all_toolsets_possible)

        # Make DAL available for OAuth cross-cluster token storage
        set_oauth_dal(dal)

        if reuse_executor:
            with self._executor_lock:
                if (
                    self._cached_tool_executor is not None
                    and self._cached_executor_key == cache_key
                ):
                    return self._cached_tool_executor
                # Build inside the lock to prevent concurrent initialization
                # for the same cache key
                toolsets = self.toolset_manager.prepare_toolsets(
                    dal=dal,
                    toolset_tag_filter=tags,
                    enable_all_toolsets_possible=enable_all_toolsets_possible,
                    prerequisite_cache=prerequisite_cache,
                    on_event=on_event,
                )
                executor = ToolExecutor(toolsets, on_event=on_event)
                self._cached_tool_executor = executor
                self._cached_executor_key = cache_key

                preload_oauth_tokens()
                eager_load_oauth_tools(executor)
                return executor

        toolsets = self.toolset_manager.prepare_toolsets(
            dal=dal,
            toolset_tag_filter=tags,
            enable_all_toolsets_possible=enable_all_toolsets_possible,
            prerequisite_cache=prerequisite_cache,
            on_event=on_event,
        )
        preload_oauth_tokens()
        executor = ToolExecutor(toolsets, on_event=on_event)
        eager_load_oauth_tools(executor)
        return executor

    def refresh_tool_executor(
        self,
        dal: Optional["SupabaseDal"] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
        enable_all_toolsets_possible: bool = False,
    ) -> list[tuple[str, str, str]]:
        """Refresh the cached tool executor and return a list of changes.

        Changes include status transitions, added toolsets, and removed toolsets.
        The cached executor is always replaced with the freshly-loaded one so that
        added/removed toolsets are picked up even when no status changes occur.
        """
        logging.info("Refreshing toolsets with tags %s and enable_all_toolsets_possible=%s", toolset_tag_filter, enable_all_toolsets_possible)
        # Normalize early so the same tags are used for both loading and caching.
        tags = toolset_tag_filter or [ToolsetTag.CORE]

        cache_key = self._executor_cache_key(tags, enable_all_toolsets_possible)
        with self._executor_lock:
            cached_executor = self._cached_tool_executor
            cached_key = self._cached_executor_key
        if not cached_executor or cached_key != cache_key:
            # Cold start or key mismatch — run live prerequisite checks.
            # Use DISABLED to avoid writing to disk (server runs on read-only fs).
            self.create_tool_executor(
                dal,
                toolset_tag_filter=tags,
                enable_all_toolsets_possible=enable_all_toolsets_possible,
                prerequisite_cache=PrerequisiteCacheMode.DISABLED,
                reuse_executor=True,
            )
            return []

        current_toolsets = cached_executor.toolsets

        new_toolsets, changes = (
            self.toolset_manager.refresh_toolsets_and_get_changes(
                current_toolsets,
                dal,
                toolset_tag_filter=tags,
                enable_all_toolsets_possible=enable_all_toolsets_possible,
            )
        )

        if changes:
            with self._executor_lock:
                executor = ToolExecutor(new_toolsets)
                preload_oauth_tokens()
                eager_load_oauth_tools(executor)
                self._cached_tool_executor = executor
                self._cached_executor_key = cache_key

        return [(name, old.value, new.value) for name, old, new in changes]

    def create_toolcalling_llm(
        self,
        dal: Optional["SupabaseDal"] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
        enable_all_toolsets_possible: bool = True,
        prerequisite_cache: PrerequisiteCacheMode = PrerequisiteCacheMode.ENABLED,
        reuse_executor: bool = False,
        model: Optional[str] = None,
        tracer=None,
        tool_results_dir: Optional[Path] = None,
        on_event: EventCallback = None,
    ) -> "ToolCallingLLM":
        """
        Create a ToolCallingLLM with explicit behavioral controls.

        Executor parameters (toolset_tag_filter, enable_all_toolsets_possible,
        prerequisite_cache, reuse_executor) are forwarded to
        :meth:`create_tool_executor`.

        Migration from removed helpers
        ------------------------------
        ``create_console_toolcalling_llm(dal, refresh_toolsets, tracer, model_name, tool_results_dir, on_event)``::

            create_toolcalling_llm(
                dal=dal,
                toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
                enable_all_toolsets_possible=True,
                prerequisite_cache=PrerequisiteCacheMode.FORCE_REFRESH if refresh_toolsets else PrerequisiteCacheMode.ENABLED,
                model=model_name,
                tracer=tracer,
                tool_results_dir=tool_results_dir,
                on_event=on_event,
            )

        ``create_agui_toolcalling_llm(dal, model, tracer, tool_results_dir)``::

            create_toolcalling_llm(
                dal=dal,
                toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
                enable_all_toolsets_possible=True,
                prerequisite_cache=PrerequisiteCacheMode.FORCE_REFRESH,
                reuse_executor=True,
                model=model,
                tracer=tracer,
                tool_results_dir=tool_results_dir,
            )
        """
        from holmes.core.tool_calling_llm import ToolCallingLLM

        # Create LLM first so model info appears during toolset loading
        llm = self._get_llm(model_key=model, tracer=tracer, on_event=on_event)
        tool_executor = self.create_tool_executor(
            dal=dal,
            toolset_tag_filter=toolset_tag_filter,
            enable_all_toolsets_possible=enable_all_toolsets_possible,
            prerequisite_cache=prerequisite_cache,
            reuse_executor=reuse_executor,
            on_event=on_event,
        )
        return ToolCallingLLM(
            tool_executor,
            self.max_steps,
            llm,
            tool_results_dir=tool_results_dir,
        )

    def validate_jira_config(self):
        if self.jira_url is None:
            raise ValueError("--jira-url must be specified")
        if not (
            self.jira_url.startswith("http://") or self.jira_url.startswith("https://")
        ):
            raise ValueError("--jira-url must start with http:// or https://")
        if self.jira_username is None:
            raise ValueError("--jira-username must be specified")
        if self.jira_api_key is None:
            raise ValueError("--jira-api-key must be specified")

    def create_jira_source(self) -> "JiraSource":
        from holmes.plugins.sources.jira import JiraSource

        self.validate_jira_config()

        return JiraSource(
            url=self.jira_url,  # type: ignore
            username=self.jira_username,  # type: ignore
            api_key=self.jira_api_key.get_secret_value(),  # type: ignore
            jql_query=self.jira_query,  # type: ignore
        )

    def create_jira_service_management_source(self) -> "JiraServiceManagementSource":
        from holmes.plugins.sources.jira import JiraServiceManagementSource

        self.validate_jira_config()

        return JiraServiceManagementSource(
            url=self.jira_url,  # type: ignore
            username=self.jira_username,  # type: ignore
            api_key=self.jira_api_key.get_secret_value(),  # type: ignore
            jql_query=self.jira_query,  # type: ignore
        )

    def create_github_source(self) -> "GitHubSource":
        from holmes.plugins.sources.github import GitHubSource

        if not self.github_url or not (
            self.github_url.startswith("http://")
            or self.github_url.startswith("https://")
        ):
            raise ValueError("--github-url must start with http:// or https://")
        if self.github_owner is None:
            raise ValueError("--github-owner must be specified")
        if self.github_repository is None:
            raise ValueError("--github-repository must be specified")
        if self.github_pat is None:
            raise ValueError("--github-pat must be specified")

        return GitHubSource(
            url=self.github_url,
            owner=self.github_owner,
            pat=self.github_pat.get_secret_value(),
            repository=self.github_repository,
            query=self.github_query,
        )

    def create_pagerduty_source(self) -> "PagerDutySource":
        from holmes.plugins.sources.pagerduty import PagerDutySource

        if self.pagerduty_api_key is None:
            raise ValueError("--pagerduty-api-key must be specified")

        return PagerDutySource(
            api_key=self.pagerduty_api_key.get_secret_value(),
            user_email=self.pagerduty_user_email,  # type: ignore
            incident_key=self.pagerduty_incident_key,
        )

    def create_opsgenie_source(self) -> "OpsGenieSource":
        from holmes.plugins.sources.opsgenie import OpsGenieSource

        if self.opsgenie_api_key is None:
            raise ValueError("--opsgenie-api-key must be specified")

        return OpsGenieSource(
            api_key=self.opsgenie_api_key.get_secret_value(),
            query=self.opsgenie_query,  # type: ignore
            team_integration_key=(
                self.opsgenie_team_integration_key.get_secret_value()
                if self.opsgenie_team_integration_key
                else None
            ),
        )

    def create_alertmanager_source(self) -> "AlertManagerSource":
        from holmes.plugins.sources.prometheus.plugin import AlertManagerSource

        return AlertManagerSource(
            url=self.alertmanager_url,  # type: ignore
            username=self.alertmanager_username,
            alertname_filter=self.alertmanager_alertname,  # type: ignore
            label_filter=self.alertmanager_label,  # type: ignore
            filepath=self.alertmanager_file,
        )

    def create_slack_destination(self) -> "SlackDestination":
        from holmes.plugins.destinations.slack import SlackDestination

        if self.slack_token is None:
            raise ValueError("--slack-token must be specified")
        if self.slack_channel is None:
            raise ValueError("--slack-channel must be specified")
        return SlackDestination(self.slack_token.get_secret_value(), self.slack_channel)

    @staticmethod
    def _format_token_count(n: int) -> str:
        """Format a token count for display: 1048576 → '1M', 32768 → '32K'."""
        if n >= 1_000_000:
            value = n / 1_000_000
            return f"{int(value)}M" if value == int(value) else f"{value:.1f}M"
        if n >= 1_000:
            value = n / 1_000
            return f"{int(value)}K" if value == int(value) else f"{value:.0f}K"
        return str(n)

    # TODO: move this to the llm model registry
    def _get_llm(
        self,
        model_key: Optional[str] = None,
        tracer=None,
        on_event: EventCallback = None,
    ) -> "DefaultLLM":
        sentry_sdk.set_tag("requested_model", model_key)
        model_entry = self.llm_model_registry.get_model_params(model_key)
        model_params = model_entry.model_dump(exclude_none=True)
        api_base = self.api_base
        api_version = self.api_version
        is_robusta_model = model_params.pop("is_robusta_model", False)
        sentry_sdk.set_tag("is_robusta_model", is_robusta_model)
        if is_robusta_model:
            # we set here the api_key since it is being refresh when exprided and not as part of the model loading.
            account_id, token = self.dal.get_ai_credentials()
            api_key = f"{account_id} {token}"
        else:
            api_key = model_params.pop("api_key", None)
            if api_key is not None:
                api_key = api_key.get_secret_value()

        model = model_params.pop("model")
        # It's ok if the model does not have api base and api version, which are defaults to None.
        # Handle both api_base and base_url - api_base takes precedence
        model_api_base = model_params.pop("api_base", None)
        model_base_url = model_params.pop("base_url", None)
        api_base = model_api_base or model_base_url or api_base
        api_version = model_params.pop("api_version", api_version)
        model_name = model_params.pop("name", None) or model_key or model
        sentry_sdk.set_tag("model_name", model_name)
        llm = DefaultLLM(
            model=model,
            api_key=api_key,
            api_base=api_base,
            api_version=api_version,
            args=model_params,
            tracer=tracer,
            name=model_name,
            is_robusta_model=is_robusta_model,
        )  # type: ignore
        context_size = self._format_token_count(llm.get_context_window_size())
        max_response = self._format_token_count(llm.get_maximum_output_token())
        if self._model_source and self._model_source != "default":
            source_hint = f"configured {self._model_source}"
        else:
            source_hint = "default, change with --model, for all options see https://holmesgpt.dev/ai-providers"
        msg = f"Model: {model_name}, {context_size} context, {max_response} max response ({source_hint})"
        display_logger.info(msg)
        if on_event is not None:
            on_event(StatusEvent(kind=StatusEventKind.MODEL_LOADED, name=model_name, message=msg))
        return llm

    def get_models_list(self) -> List[str]:
        if self.llm_model_registry and self.llm_model_registry.models:
            return list(self.llm_model_registry.models.keys())

        return []


class TicketSource(BaseModel):
    config: Config
    output_instructions: list[str]
    source: Union["JiraServiceManagementSource", "PagerDutySource"]

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SourceFactory(BaseModel):
    @staticmethod
    def create_source(
        source: SupportedTicketSources,
        config_file: Optional[Path],
        ticket_url: Optional[str],
        ticket_username: Optional[str],
        ticket_api_key: Optional[str],
        ticket_id: Optional[str],
        model: Optional[str] = None,
    ) -> TicketSource:
        from holmes.plugins.sources.jira import JiraServiceManagementSource
        from holmes.plugins.sources.pagerduty import PagerDutySource

        TicketSource.model_rebuild()
        supported_sources = [s.value for s in SupportedTicketSources]
        if source not in supported_sources:
            raise ValueError(
                f"Source '{source}' is not supported. Supported sources: {', '.join(supported_sources)}"
            )

        if source == SupportedTicketSources.JIRA_SERVICE_MANAGEMENT:
            config = Config.load_from_file(
                config_file=config_file,
                api_key=None,
                model=model,
                max_steps=None,
                jira_url=ticket_url,
                jira_username=ticket_username,
                jira_api_key=ticket_api_key,
                jira_query=None,
                custom_toolsets=None,
            )

            if not (
                config.jira_url
                and config.jira_username
                and config.jira_api_key
                and ticket_id
            ):
                raise ValueError(
                    "URL, username, API key, and ticket ID are required for jira-service-management"
                )

            output_instructions = [
                "All output links/urls must **always** be of this format : [link text here|http://your.url.here.com] and **never*** the format [link text here](http://your.url.here.com)"
            ]
            source_instance = config.create_jira_service_management_source()
            return TicketSource(
                config=config,
                output_instructions=output_instructions,
                source=source_instance,
            )

        elif source == SupportedTicketSources.PAGERDUTY:
            config = Config.load_from_file(
                config_file=config_file,
                api_key=None,
                model=model,
                max_steps=None,
                pagerduty_api_key=ticket_api_key,
                pagerduty_user_email=ticket_username,
                pagerduty_incident_key=None,
                custom_toolsets=None,
            )

            if not (
                config.pagerduty_user_email and config.pagerduty_api_key and ticket_id
            ):
                raise ValueError(
                    "username, API key, and ticket ID are required for pagerduty"
                )

            output_instructions = [
                "All output links/urls must **always** be of this format : \n link text here: http://your.url.here.com\n **never*** use the url the format [link text here](http://your.url.here.com)"
            ]
            source_instance = config.create_pagerduty_source()  # type: ignore
            return TicketSource(
                config=config,
                output_instructions=output_instructions,
                source=source_instance,
            )

        else:
            raise NotImplementedError(f"Source '{source}' is not yet implemented")
