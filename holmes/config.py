import logging
import os
import os.path
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Union

import sentry_sdk
import yaml  # type: ignore
from pydantic import BaseModel, ConfigDict, FilePath, PrivateAttr, SecretStr

from holmes.common.env_vars import ROBUSTA_CONFIG_PATH
from holmes.core.llm import DefaultLLM, LLMModelRegistry
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.toolset_manager import ToolsetManager
from holmes.plugins.runbooks import (
    RunbookCatalog,
    load_builtin_runbooks,
    load_runbook_catalog,
    load_runbooks_from_file,
)

# Source plugin imports moved to their respective create methods to speed up startup
if TYPE_CHECKING:
    from holmes.core.tool_calling_llm import IssueInvestigator, ToolCallingLLM
    from holmes.plugins.destinations.slack import SlackDestination
    from holmes.plugins.sources.github import GitHubSource
    from holmes.plugins.sources.jira import JiraServiceManagementSource, JiraSource
    from holmes.plugins.sources.opsgenie import OpsGenieSource
    from holmes.plugins.sources.pagerduty import PagerDutySource
    from holmes.plugins.sources.prometheus.plugin import AlertManagerSource

from holmes.core.config import config_path_dir
from holmes.core.supabase_dal import SupabaseDal
from holmes.utils.definitions import RobustaConfig
from holmes.utils.pydantic_utils import RobustaBaseConfig, load_model_from_file

DEFAULT_CONFIG_LOCATION = os.path.join(config_path_dir, "config.yaml")


class SupportedTicketSources(str, Enum):
    JIRA_SERVICE_MANAGEMENT = "jira-service-management"
    PAGERDUTY = "pagerduty"


class Config(RobustaBaseConfig):
    model: Optional[str] = None
    api_key: Optional[SecretStr] = (
        None  # if None, read from OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT env var
    )
    api_base: Optional[str] = None
    api_version: Optional[str] = None
    fast_model: Optional[str] = None
    max_steps: int = 40
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

    custom_runbooks: List[FilePath] = []
    custom_runbook_catalogs: List[Union[str, FilePath]] = []

    # custom_toolsets is passed from config file, and be used to override built-in toolsets, provides 'stable' customized toolset.
    # The status of custom toolsets can be cached.
    custom_toolsets: Optional[List[FilePath]] = None
    # custom_toolsets_from_cli is passed from CLI option `--custom-toolsets` as 'experimental' custom toolsets.
    # The status of toolset here won't be cached, so the toolset from cli will always be loaded when specified in the CLI.
    custom_toolsets_from_cli: Optional[List[FilePath]] = None
    # if True, we will try to load the Robusta AI model, in cli we aren't trying to load it.
    should_try_robusta_ai: bool = False

    toolsets: Optional[dict[str, dict[str, Any]]] = None
    mcp_servers: Optional[dict[str, dict[str, Any]]] = None

    _server_tool_executor: Optional[ToolExecutor] = None
    _agui_tool_executor: Optional[ToolExecutor] = None

    # TODO: Separate those fields to facade class, this shouldn't be part of the config.
    _toolset_manager: Optional[ToolsetManager] = PrivateAttr(None)
    _llm_model_registry: Optional[LLMModelRegistry] = PrivateAttr(None)
    _dal: Optional[SupabaseDal] = PrivateAttr(None)

    @property
    def toolset_manager(self) -> ToolsetManager:
        if not self._toolset_manager:
            self._toolset_manager = ToolsetManager(
                toolsets=self.toolsets,
                mcp_servers=self.mcp_servers,
                custom_toolsets=self.custom_toolsets,
                custom_toolsets_from_cli=self.custom_toolsets_from_cli,
                global_fast_model=self.fast_model,
                custom_runbook_catalogs=self.custom_runbook_catalogs,
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
            logging.info(
                f"Loaded models: {list(self.llm_model_registry.models.keys())}"
            )
        else:
            logging.warning("No llm models were loaded")

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
            # TODO
            # custom_runbooks
        ]:
            val = os.getenv(field_name.upper(), None)
            if val is not None:
                kwargs[field_name] = val
        kwargs["cluster_name"] = Config.__get_cluster_name()
        kwargs["should_try_robusta_ai"] = True
        result = cls(**kwargs)
        result.log_useful_info()
        return result

    @staticmethod
    def __get_cluster_name() -> Optional[str]:
        config_file_path = ROBUSTA_CONFIG_PATH
        env_cluster_name = os.environ.get("CLUSTER_NAME")
        if env_cluster_name:
            return env_cluster_name

        if not os.path.exists(config_file_path):
            logging.info(f"No robusta config in {config_file_path}")
            return None

        logging.info(f"loading config {config_file_path}")
        with open(config_file_path) as file:
            yaml_content = yaml.safe_load(file)
            config = RobustaConfig(**yaml_content)
            return config.global_config.get("cluster_name")

        return None

    def get_runbook_catalog(self) -> Optional[RunbookCatalog]:
        runbook_catalog = load_runbook_catalog(
            dal=self.dal, custom_catalog_paths=self.custom_runbook_catalogs
        )
        return runbook_catalog

    def create_console_tool_executor(
        self, dal: Optional["SupabaseDal"], refresh_status: bool = False
    ) -> ToolExecutor:
        """
        Creates a ToolExecutor instance configured for CLI usage. This executor manages the available tools
        and their execution in the command-line interface.

        The method loads toolsets in this order, with later sources overriding earlier ones:
        1. Built-in toolsets (tagged as CORE or CLI)
        2. toolsets from config file will override and be merged into built-in toolsets with the same name.
        3. Custom toolsets from config files which can not override built-in toolsets
        """
        cli_toolsets = self.toolset_manager.list_console_toolsets(
            dal=dal, refresh_status=refresh_status
        )
        return ToolExecutor(cli_toolsets)

    def create_agui_tool_executor(self, dal: Optional["SupabaseDal"]) -> ToolExecutor:
        """
        Creates ToolExecutor for the AG-UI server endpoints
        """

        if self._agui_tool_executor:
            return self._agui_tool_executor

        # Use same toolset as CLI for AG-UI front-end.
        agui_toolsets = self.toolset_manager.list_console_toolsets(
            dal=dal, refresh_status=True
        )

        self._agui_tool_executor = ToolExecutor(agui_toolsets)

        return self._agui_tool_executor

    def create_tool_executor(self, dal: Optional["SupabaseDal"]) -> ToolExecutor:
        """
        Creates ToolExecutor for the server endpoints
        """

        if self._server_tool_executor:
            return self._server_tool_executor

        toolsets = self.toolset_manager.list_server_toolsets(dal=dal)

        self._server_tool_executor = ToolExecutor(toolsets)

        logging.debug(
            f"Starting AI session with tools: {[tn for tn in self._server_tool_executor.tools_by_name.keys()]}"
        )

        return self._server_tool_executor

    def create_console_toolcalling_llm(
        self,
        dal: Optional["SupabaseDal"] = None,
        refresh_toolsets: bool = False,
        tracer=None,
        model_name: Optional[str] = None,
    ) -> "ToolCallingLLM":
        tool_executor = self.create_console_tool_executor(dal, refresh_toolsets)
        from holmes.core.tool_calling_llm import ToolCallingLLM

        return ToolCallingLLM(
            tool_executor,
            self.max_steps,
            self._get_llm(tracer=tracer, model_key=model_name),
        )

    def create_agui_toolcalling_llm(
        self,
        dal: Optional["SupabaseDal"] = None,
        model: Optional[str] = None,
        tracer=None,
    ) -> "ToolCallingLLM":
        tool_executor = self.create_agui_tool_executor(dal)
        from holmes.core.tool_calling_llm import ToolCallingLLM

        return ToolCallingLLM(
            tool_executor, self.max_steps, self._get_llm(model, tracer)
        )

    def create_toolcalling_llm(
        self,
        dal: Optional["SupabaseDal"] = None,
        model: Optional[str] = None,
        tracer=None,
    ) -> "ToolCallingLLM":
        tool_executor = self.create_tool_executor(dal)
        from holmes.core.tool_calling_llm import ToolCallingLLM

        return ToolCallingLLM(
            tool_executor, self.max_steps, self._get_llm(model, tracer)
        )

    def create_issue_investigator(
        self,
        dal: Optional["SupabaseDal"] = None,
        model: Optional[str] = None,
        tracer=None,
    ) -> "IssueInvestigator":
        all_runbooks = load_builtin_runbooks()
        for runbook_path in self.custom_runbooks:
            all_runbooks.extend(load_runbooks_from_file(runbook_path))

        from holmes.core.runbooks import RunbookManager

        runbook_manager = RunbookManager(all_runbooks)
        tool_executor = self.create_tool_executor(dal)
        from holmes.core.tool_calling_llm import IssueInvestigator

        return IssueInvestigator(
            tool_executor=tool_executor,
            runbook_manager=runbook_manager,
            max_steps=self.max_steps,
            llm=self._get_llm(model, tracer),
            cluster_name=self.cluster_name,
        )

    def create_console_issue_investigator(
        self, dal: Optional["SupabaseDal"] = None, model_name: Optional[str] = None
    ) -> "IssueInvestigator":
        all_runbooks = load_builtin_runbooks()
        for runbook_path in self.custom_runbooks:
            all_runbooks.extend(load_runbooks_from_file(runbook_path))

        from holmes.core.runbooks import RunbookManager

        runbook_manager = RunbookManager(all_runbooks)
        tool_executor = self.create_console_tool_executor(dal=dal)
        from holmes.core.tool_calling_llm import IssueInvestigator

        return IssueInvestigator(
            tool_executor=tool_executor,
            runbook_manager=runbook_manager,
            max_steps=self.max_steps,
            llm=self._get_llm(model_key=model_name),
            cluster_name=self.cluster_name,
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

    # TODO: move this to the llm model registry
    def _get_llm(self, model_key: Optional[str] = None, tracer=None) -> "DefaultLLM":
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
        logging.info(
            f"Using model: {model_name} ({llm.get_context_window_size():,} total tokens, {llm.get_maximum_output_token():,} output tokens)"
        )
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
    ) -> TicketSource:
        supported_sources = [s.value for s in SupportedTicketSources]
        if source not in supported_sources:
            raise ValueError(
                f"Source '{source}' is not supported. Supported sources: {', '.join(supported_sources)}"
            )

        if source == SupportedTicketSources.JIRA_SERVICE_MANAGEMENT:
            config = Config.load_from_file(
                config_file=config_file,
                api_key=None,
                model=None,
                max_steps=None,
                jira_url=ticket_url,
                jira_username=ticket_username,
                jira_api_key=ticket_api_key,
                jira_query=None,
                custom_toolsets=None,
                custom_runbooks=None,
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
                model=None,
                max_steps=None,
                pagerduty_api_key=ticket_api_key,
                pagerduty_user_email=ticket_username,
                pagerduty_incident_key=None,
                custom_toolsets=None,
                custom_runbooks=None,
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
