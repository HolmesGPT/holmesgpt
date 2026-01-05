import concurrent.futures
import json
import logging
import os
from typing import TYPE_CHECKING, Any, List, Optional, Union

from benedict import benedict
from pydantic import FilePath

from holmes.core.config import config_path_dir
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import Toolset, ToolsetStatusEnum, ToolsetTag, ToolsetType
from holmes.plugins.toolsets import load_builtin_toolsets, load_toolsets_from_config
from holmes.utils.definitions import CUSTOM_TOOLSET_LOCATION

if TYPE_CHECKING:
    pass

DEFAULT_TOOLSET_STATUS_LOCATION = os.path.join(config_path_dir, "toolsets_status.json")

# Mapping of deprecated toolset names to their new names
DEPRECATED_TOOLSET_NAMES: dict[str, str] = {
    "coralogix/logs": "coralogix",
}


def handle_deprecated_toolset_name(
    toolset_name: str, builtin_toolset_names: list[str]
) -> str:
    if toolset_name in DEPRECATED_TOOLSET_NAMES:
        new_name = DEPRECATED_TOOLSET_NAMES[toolset_name]
        if new_name in builtin_toolset_names:
            logging.warning(
                f"The toolset name '{toolset_name}' is deprecated. "
                f"Please use '{new_name}' instead. "
                "The old name will continue to work but may be removed in a future version."
            )
            return new_name
    return toolset_name


class ToolsetManager:
    """
    ToolsetManager is responsible for managing toolset locally.
    It can refresh the status of all toolsets and cache the status to a file.
    It also provides methods to get toolsets by name and to get the list of all toolsets.
    """

    def __init__(
        self,
        toolsets: Optional[dict[str, dict[str, Any]]] = None,
        mcp_servers: Optional[dict[str, dict[str, Any]]] = None,
        custom_toolsets: Optional[List[FilePath]] = None,
        custom_toolsets_from_cli: Optional[List[FilePath]] = None,
        toolset_status_location: Optional[FilePath] = None,
        global_fast_model: Optional[str] = None,
        custom_runbook_catalogs: Optional[List[Union[str, FilePath]]] = None,
    ):
        self.toolsets = toolsets
        self.toolsets = toolsets or {}
        self.custom_runbook_catalogs = custom_runbook_catalogs
        if mcp_servers is not None:
            for _, mcp_server in mcp_servers.items():
                mcp_server["type"] = ToolsetType.MCP.value
        self.toolsets.update(mcp_servers or {})
        self.custom_toolsets = custom_toolsets
        self.global_fast_model = global_fast_model

        if toolset_status_location is None:
            toolset_status_location = FilePath(DEFAULT_TOOLSET_STATUS_LOCATION)

        # holmes container uses CUSTOM_TOOLSET_LOCATION to load custom toolsets
        if os.path.isfile(CUSTOM_TOOLSET_LOCATION):
            if self.custom_toolsets is None:
                self.custom_toolsets = []
            self.custom_toolsets.append(FilePath(CUSTOM_TOOLSET_LOCATION))

        self.custom_toolsets_from_cli = custom_toolsets_from_cli
        self.toolset_status_location = toolset_status_location

    @property
    def cli_tool_tags(self) -> List[ToolsetTag]:
        """
        Returns the list of toolset tags that are relevant for CLI tools.
        """
        return [ToolsetTag.CORE, ToolsetTag.CLI]

    @property
    def server_tool_tags(self) -> List[ToolsetTag]:
        """
        Returns the list of toolset tags that are relevant for server tools.
        """
        return [ToolsetTag.CORE, ToolsetTag.CLUSTER]

    def _list_all_toolsets(
        self,
        dal: Optional[SupabaseDal] = None,
        check_prerequisites=True,
        enable_all_toolsets=False,
        toolset_tags: Optional[List[ToolsetTag]] = None,
    ) -> List[Toolset]:
        """
        List all built-in and custom toolsets.

        The method loads toolsets in this order, with later sources overriding earlier ones:
        1. Built-in toolsets
        2. Toolsets defined in self.toolsets can override both built-in and add new custom toolsets
        3. custom toolset from config can override both built-in and add new custom toolsets # for backward compatibility
        """
        # Load built-in toolsets
        # Extract search paths from custom catalog files
        additional_search_paths = None
        if self.custom_runbook_catalogs:
            additional_search_paths = [
                os.path.dirname(os.path.abspath(str(catalog_path)))
                for catalog_path in self.custom_runbook_catalogs
            ]

        builtin_toolsets = load_builtin_toolsets(dal, additional_search_paths)
        toolsets_by_name: dict[str, Toolset] = {
            toolset.name: toolset for toolset in builtin_toolsets
        }
        builtin_toolsets_names = list(toolsets_by_name.keys())

        if enable_all_toolsets:
            for toolset in toolsets_by_name.values():
                toolset.enabled = True

        # build-in toolset is enabled when it's explicitly enabled in the toolset or custom toolset config
        if self.toolsets is not None:
            toolsets_from_config = self._load_toolsets_from_config(
                self.toolsets, builtin_toolsets_names, dal
            )

            if toolsets_from_config:
                self.add_or_merge_onto_toolsets(
                    toolsets_from_config,
                    toolsets_by_name,
                )

        # custom toolset should not override built-in toolsets
        # to test the new change of built-in toolset, we should make code change and re-compile the program
        custom_toolsets = self.load_custom_toolsets(builtin_toolsets_names)
        self.add_or_merge_onto_toolsets(
            custom_toolsets,
            toolsets_by_name,
        )

        if toolset_tags is not None:
            toolsets_by_name = {
                name: toolset
                for name, toolset in toolsets_by_name.items()
                if any(tag in toolset_tags for tag in toolset.tags)
            }

        # Inject global fast_model into all toolsets
        final_toolsets = list(toolsets_by_name.values())
        self._inject_fast_model_into_transformers(final_toolsets)

        # check_prerequisites against each enabled toolset
        if not check_prerequisites:
            return final_toolsets

        enabled_toolsets: List[Toolset] = []
        for _, toolset in toolsets_by_name.items():
            if toolset.enabled:
                enabled_toolsets.append(toolset)
            else:
                toolset.status = ToolsetStatusEnum.DISABLED
        self.check_toolset_prerequisites(enabled_toolsets)

        return final_toolsets

    @classmethod
    def check_toolset_prerequisites(cls, toolsets: list[Toolset]):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for toolset in toolsets:
                futures.append(executor.submit(toolset.check_prerequisites))

            for _ in concurrent.futures.as_completed(futures):
                pass

    def _load_toolsets_from_config(
        self,
        toolsets: dict[str, dict[str, Any]],
        builtin_toolset_names: list[str],
        dal: Optional[SupabaseDal] = None,
    ) -> List[Toolset]:
        if toolsets is None:
            logging.debug("No toolsets configured, skipping loading toolsets")
            return []

        builtin_toolsets_dict: dict[str, dict[str, Any]] = {}
        custom_toolsets_dict: dict[str, dict[str, Any]] = {}
        for toolset_name, toolset_config in toolsets.items():
            toolset_name = handle_deprecated_toolset_name(
                toolset_name, builtin_toolset_names
            )

            if toolset_name in builtin_toolset_names:
                # build-in types was assigned when loaded
                builtin_toolsets_dict[toolset_name] = toolset_config
            else:
                if toolset_config.get("type") is None:
                    toolset_config["type"] = ToolsetType.CUSTOMIZED.value
                # custom toolsets defaults to enabled when not explicitly disabled
                if toolset_config.get("enabled", True) is False:
                    toolset_config["enabled"] = False
                else:
                    toolset_config["enabled"] = True
                custom_toolsets_dict[toolset_name] = toolset_config

        # built-in toolsets and built-in MCP servers in the config can override the existing fields of built-in toolsets
        builtin_toolsets = load_toolsets_from_config(
            builtin_toolsets_dict, strict_check=False
        )
        # custom toolsets or MCP servers are expected to defined required fields
        custom_toolsets = load_toolsets_from_config(
            toolsets=custom_toolsets_dict, strict_check=True
        )

        return builtin_toolsets + custom_toolsets

    def refresh_toolset_status(
        self,
        dal: Optional[SupabaseDal] = None,
        enable_all_toolsets=False,
        toolset_tags: Optional[List[ToolsetTag]] = None,
    ):
        """
        Refresh the status of all toolsets and cache the status to a file.
        Loading cached toolsets status saves the time for runtime tool executor checking the status of each toolset

        enabled toolset when:
        - build-in toolset specified in the config and not explicitly disabled
        - custom toolset not explicitly disabled
        """

        all_toolsets = self._list_all_toolsets(
            dal=dal,
            check_prerequisites=True,
            enable_all_toolsets=enable_all_toolsets,
            toolset_tags=toolset_tags,
        )

        if self.toolset_status_location and not os.path.exists(
            os.path.dirname(self.toolset_status_location)
        ):
            os.makedirs(os.path.dirname(self.toolset_status_location))
        with open(self.toolset_status_location, "w") as f:
            toolset_status = [
                json.loads(
                    toolset.model_dump_json(
                        include={"name", "status", "enabled", "type", "path", "error"}
                    )
                )
                for toolset in all_toolsets
            ]
            json.dump(toolset_status, f, indent=2)
        logging.info(f"Toolset statuses are cached to {self.toolset_status_location}")

    def load_toolset_with_status(
        self,
        dal: Optional[SupabaseDal] = None,
        refresh_status: bool = False,
        enable_all_toolsets=False,
        toolset_tags: Optional[List[ToolsetTag]] = None,
    ) -> List[Toolset]:
        """
        Load the toolset with status from the cache file.
        1. load the built-in toolsets
        2. load the custom toolsets from config, and override the built-in toolsets
        3. load the custom toolsets from CLI, and raise error if the custom toolset from CLI conflicts with existing toolsets
        """

        if not os.path.exists(self.toolset_status_location) or refresh_status:
            logging.info("Refreshing available datasources (toolsets)")
            self.refresh_toolset_status(
                dal, enable_all_toolsets=enable_all_toolsets, toolset_tags=toolset_tags
            )
            using_cached = False
        else:
            using_cached = True

        cached_toolsets: List[dict[str, Any]] = []
        with open(self.toolset_status_location, "r") as f:
            cached_toolsets = json.load(f)

        # load status from cached file and update the toolset details
        toolsets_status_by_name: dict[str, dict[str, Any]] = {
            cached_toolset["name"]: cached_toolset for cached_toolset in cached_toolsets
        }
        all_toolsets_with_status = self._list_all_toolsets(
            dal=dal, check_prerequisites=False, toolset_tags=toolset_tags
        )

        enabled_toolsets_from_cache: List[Toolset] = []
        for toolset in all_toolsets_with_status:
            if toolset.name in toolsets_status_by_name:
                # Update the status and error from the cached status
                cached_status = toolsets_status_by_name[toolset.name]
                toolset.status = ToolsetStatusEnum(cached_status["status"])
                toolset.error = cached_status.get("error", None)
                toolset.enabled = cached_status.get("enabled", True)
                toolset.type = ToolsetType(
                    cached_status.get("type", ToolsetType.BUILTIN.value)
                )
                toolset.path = cached_status.get("path", None)
            # check prerequisites for only enabled toolset when the toolset is loaded from cache. When the toolset is
            # not loaded from cache, the prerequisites are checked in the refresh_toolset_status method.
            if toolset.enabled and (
                toolset.status == ToolsetStatusEnum.ENABLED
                or toolset.type == ToolsetType.MCP
            ):
                # MCP servers need to reload their tools even if previously failed, so rerun prerequisites
                enabled_toolsets_from_cache.append(toolset)
        self.check_toolset_prerequisites(enabled_toolsets_from_cache)

        # CLI custom toolsets status are not cached, and their prerequisites are always checked whenever the CLI runs.
        custom_toolsets_from_cli = self._load_toolsets_from_paths(
            self.custom_toolsets_from_cli,
            list(toolsets_status_by_name.keys()),
            check_conflict_default=True,
        )

        # Inject fast_model into CLI custom toolsets
        self._inject_fast_model_into_transformers(custom_toolsets_from_cli)

        # custom toolsets from cli as experimental toolset should not override custom toolsets from config
        enabled_toolsets_from_cli: List[Toolset] = []
        for custom_toolset_from_cli in custom_toolsets_from_cli:
            if custom_toolset_from_cli.name in toolsets_status_by_name:
                raise ValueError(
                    f"Toolset {custom_toolset_from_cli.name} from cli is already defined in existing toolset"
                )
            enabled_toolsets_from_cli.append(custom_toolset_from_cli)
        # status of custom toolsets from cli is not cached, and we need to check prerequisites every time the cli runs.
        self.check_toolset_prerequisites(enabled_toolsets_from_cli)

        all_toolsets_with_status.extend(custom_toolsets_from_cli)
        if using_cached:
            num_available_toolsets = len(
                [toolset for toolset in all_toolsets_with_status if toolset.enabled]
            )
            logging.info(
                f"Using {num_available_toolsets} datasources (toolsets). To refresh: use flag `--refresh-toolsets`"
            )
        return all_toolsets_with_status

    def list_console_toolsets(
        self, dal: Optional[SupabaseDal] = None, refresh_status=False
    ) -> List[Toolset]:
        """
        List all enabled toolsets that cli tools can use.

        listing console toolset does not refresh toolset status by default, and expects the status to be
        refreshed specifically and cached locally.
        """
        toolsets_with_status = self.load_toolset_with_status(
            dal,
            refresh_status=refresh_status,
            enable_all_toolsets=True,
            toolset_tags=self.cli_tool_tags,
        )
        return toolsets_with_status

    # TODO(mainred): cache and refresh periodically toolset status for server if necessary
    def list_server_toolsets(
        self, dal: Optional[SupabaseDal] = None, refresh_status=True
    ) -> List[Toolset]:
        """
        List all toolsets that are enabled and have the server tool tags.

        server will sync the status of toolsets to DB during startup instead of local cache.
        Refreshing the status by default for server to keep the toolsets up-to-date instead of relying on local cache.
        """
        toolsets_with_status = self._list_all_toolsets(
            dal,
            check_prerequisites=True,
            enable_all_toolsets=False,
            toolset_tags=self.server_tool_tags,
        )
        return toolsets_with_status

    def _load_toolsets_from_paths(
        self,
        toolset_paths: Optional[List[FilePath]],
        builtin_toolsets_names: list[str],
        check_conflict_default: bool = False,
    ) -> List[Toolset]:
        if not toolset_paths:
            logging.debug("No toolsets configured, skipping loading toolsets")
            return []

        loaded_custom_toolsets: List[Toolset] = []
        for toolset_path in toolset_paths:
            if not os.path.isfile(toolset_path):
                raise FileNotFoundError(f"toolset file {toolset_path} does not exist")

            try:
                parsed_yaml = benedict(toolset_path)
            except Exception as e:
                raise ValueError(
                    f"Failed to load toolsets from {toolset_path}, error: {e}"
                ) from e
            toolsets_config: dict[str, dict[str, Any]] = parsed_yaml.get("toolsets", {})
            mcp_config: dict[str, dict[str, Any]] = parsed_yaml.get("mcp_servers", {})

            for server_config in mcp_config.values():
                server_config["type"] = ToolsetType.MCP.value

            for toolset_config in toolsets_config.values():
                toolset_config["path"] = toolset_path

            toolsets_config.update(mcp_config)

            if not toolsets_config:
                raise ValueError(
                    f"No 'toolsets' or 'mcp_servers' key found in: {toolset_path}"
                )

            toolsets_from_config = self._load_toolsets_from_config(
                toolsets_config, builtin_toolsets_names
            )
            if check_conflict_default:
                for toolset in toolsets_from_config:
                    if toolset.name in builtin_toolsets_names:
                        raise Exception(
                            f"Toolset {toolset.name} is already defined in the built-in toolsets. "
                            "Please rename the custom toolset or remove it from the custom toolsets configuration."
                        )

            loaded_custom_toolsets.extend(toolsets_from_config)

        return loaded_custom_toolsets

    def load_custom_toolsets(self, builtin_toolsets_names: list[str]) -> list[Toolset]:
        """
        Loads toolsets config from custom toolset path with YAMLToolset class.

        Example configuration:
        # override the built-in toolsets with custom toolsets
        kubernetes/logs:
            enabled: false

        # define a custom toolset with strictly defined fields
        test/configurations:
            enabled: true
            icon_url: "example.com"
            description: "test_description"
            docs_url: "https://docs.docker.com/"
            prerequisites:
                - env:
                    - API_ENDPOINT
                - command: "curl ${API_ENDPOINT}"
            additional_instructions: "jq -r '.result.results[].userData | fromjson | .text | fromjson | .log'"
            tools:
                - name: "curl_example"
                  description: "Perform a curl request to example.com using variables"
                  command: "curl -X GET '{{api_endpoint}}?query={{ query_param }}' "
        """
        if not self.custom_toolsets and not self.custom_toolsets_from_cli:
            logging.debug(
                "No custom toolsets configured, skipping loading custom toolsets"
            )
            return []

        loaded_custom_toolsets: List[Toolset] = []
        custom_toolsets = self._load_toolsets_from_paths(
            self.custom_toolsets, builtin_toolsets_names
        )
        loaded_custom_toolsets.extend(custom_toolsets)

        return loaded_custom_toolsets

    def add_or_merge_onto_toolsets(
        self,
        new_toolsets: list[Toolset],
        existing_toolsets_by_name: dict[str, Toolset],
    ) -> None:
        """
        Add new or merge toolsets onto existing toolsets.
        """

        for new_toolset in new_toolsets:
            if new_toolset.name in existing_toolsets_by_name.keys():
                existing_toolsets_by_name[new_toolset.name].override_with(new_toolset)
            else:
                existing_toolsets_by_name[new_toolset.name] = new_toolset
                existing_toolsets_by_name[new_toolset.name] = new_toolset

    def _inject_fast_model_into_transformers(self, toolsets: List[Toolset]) -> None:
        """
        Inject global fast_model setting into all llm_summarize transformers that don't already have fast_model.
        This ensures --fast-model reaches all tools regardless of toolset-level transformer configuration.

        IMPORTANT: This also forces recreation of transformer instances since they may already be created.
        """
        import logging

        from holmes.core.transformers import registry

        logger = logging.getLogger(__name__)

        logger.debug(
            f"Starting fast_model injection. global_fast_model={self.global_fast_model}"
        )

        if not self.global_fast_model:
            logger.debug("No global_fast_model configured, skipping injection")
            return

        injected_count = 0
        toolset_count = 0

        for toolset in toolsets:
            toolset_count += 1
            toolset_injected = 0
            logger.debug(
                f"Processing toolset '{toolset.name}', has toolset transformers: {toolset.transformers is not None}"
            )

            # Inject into toolset-level transformers
            if toolset.transformers:
                logger.debug(
                    f"Toolset '{toolset.name}' has {len(toolset.transformers)} toolset-level transformers"
                )
                for transformer in toolset.transformers:
                    logger.debug(
                        f"  Toolset transformer: name='{transformer.name}', config keys={list(transformer.config.keys())}"
                    )
                    if (
                        transformer.name == "llm_summarize"
                        and "fast_model" not in transformer.config
                    ):
                        transformer.config["global_fast_model"] = self.global_fast_model
                        injected_count += 1
                        toolset_injected += 1
                        logger.info(
                            f"  ✓ Injected global_fast_model into toolset '{toolset.name}' transformer"
                        )
                    elif transformer.name == "llm_summarize":
                        logger.debug(
                            f"  - Toolset transformer already has fast_model: {transformer.config.get('fast_model')}"
                        )
            else:
                logger.debug(
                    f"Toolset '{toolset.name}' has no toolset-level transformers"
                )

            # Inject into tool-level transformers
            if hasattr(toolset, "tools") and toolset.tools:
                logger.debug(f"Toolset '{toolset.name}' has {len(toolset.tools)} tools")
                for tool in toolset.tools:
                    logger.debug(
                        f"  Processing tool '{tool.name}', has transformers: {tool.transformers is not None}"
                    )
                    if tool.transformers:
                        logger.debug(
                            f"    Tool '{tool.name}' has {len(tool.transformers)} transformers"
                        )
                        tool_updated = False
                        for transformer in tool.transformers:
                            logger.debug(
                                f"      Tool transformer: name='{transformer.name}', config keys={list(transformer.config.keys())}"
                            )
                            if (
                                transformer.name == "llm_summarize"
                                and "fast_model" not in transformer.config
                            ):
                                transformer.config["global_fast_model"] = (
                                    self.global_fast_model
                                )
                                injected_count += 1
                                toolset_injected += 1
                                tool_updated = True
                                logger.info(
                                    f"      ✓ Injected global_fast_model into tool '{tool.name}' transformer"
                                )
                            elif transformer.name == "llm_summarize":
                                logger.debug(
                                    f"      - Tool transformer already has fast_model: {transformer.config.get('fast_model')}"
                                )

                        # CRITICAL: Force recreation of transformer instances if we updated the config
                        if tool_updated:
                            logger.info(
                                f"      🔄 Recreating transformer instances for tool '{tool.name}' after injection"
                            )
                            if tool.transformers:
                                tool._transformer_instances = []
                                for transformer in tool.transformers:
                                    if not transformer:
                                        continue
                                    try:
                                        # Create transformer instance with updated config
                                        transformer_instance = (
                                            registry.create_transformer(
                                                transformer.name, transformer.config
                                            )
                                        )
                                        tool._transformer_instances.append(
                                            transformer_instance
                                        )
                                        logger.debug(
                                            f"        Recreated transformer '{transformer.name}' for tool '{tool.name}' with config: {transformer.config}"
                                        )
                                    except Exception as e:
                                        logger.warning(
                                            f"        Failed to recreate transformer '{transformer.name}' for tool '{tool.name}': {e}"
                                        )
                                        continue
                    else:
                        logger.debug(f"    Tool '{tool.name}' has no transformers")
            else:
                logger.debug(f"Toolset '{toolset.name}' has no tools")

            if toolset_injected > 0:
                logger.info(
                    f"Toolset '{toolset.name}': injected into {toolset_injected} transformers"
                )

        logger.info(
            f"Fast_model injection complete: {injected_count} transformers updated across {toolset_count} toolsets"
        )
