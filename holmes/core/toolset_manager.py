import concurrent.futures
import json
import logging
import os
from pathlib import Path
from typing import Any, List, Optional, Union

from pydantic import FilePath

from holmes.core.config import config_path_dir
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import PrerequisiteCacheMode, Toolset, ToolsetStatusEnum, ToolsetTag, ToolsetType
from holmes.core.toolset_registry import (
    ToolsetRegistry,
    _merge_onto,
)
from holmes.utils.config_hash import check_and_update_config_hashes
from holmes.utils.definitions import CUSTOM_TOOLSET_LOCATION

DEFAULT_TOOLSET_STATUS_LOCATION = os.path.join(config_path_dir, "toolsets_status.json")


class ToolsetManager:
    """Manages toolset lifecycle: prerequisites, caching, and status.

    Uses a :class:`ToolsetRegistry` for discovering, loading, merging toolsets
    and deciding which are enabled. The manager then handles:
    - Prerequisite checking (eager and lazy)
    - Status caching to disk
    - CLI toolset conflict checking
    - Fast-model injection into transformers
    """

    def __init__(
        self,
        toolsets: Optional[dict[str, dict[str, Any]]] = None,
        mcp_servers: Optional[dict[str, dict[str, Any]]] = None,
        custom_toolsets: Optional[List[FilePath]] = None,
        custom_toolsets_from_cli: Optional[List[FilePath]] = None,
        toolset_status_location: Optional[FilePath] = None,
        custom_runbook_catalogs: Optional[List[Union[str, FilePath]]] = None,
        config_file_path: Optional[Path] = None,
        additional_toolsets: Optional[List[Toolset]] = None,
    ):
        # Build the merged toolsets config (merge MCP servers into toolsets dict)
        toolsets_config = toolsets or {}
        if mcp_servers is not None:
            for _, mcp_server in mcp_servers.items():
                mcp_server["type"] = ToolsetType.MCP.value
        toolsets_config.update(mcp_servers or {})

        # Collect custom toolset file paths
        custom_toolset_paths: List[FilePath] = list(custom_toolsets or [])
        if os.path.isfile(CUSTOM_TOOLSET_LOCATION):
            custom_toolset_paths.append(FilePath(CUSTOM_TOOLSET_LOCATION))

        self.registry = ToolsetRegistry(
            toolsets_config=toolsets_config,
            custom_toolset_paths=custom_toolset_paths,
            additional_toolsets=additional_toolsets or [],
            custom_runbook_catalogs=custom_runbook_catalogs,
        )

        self.custom_toolsets_from_cli = custom_toolsets_from_cli
        self.config_file_path = config_file_path
        # Keep reference to custom_toolset_paths for hash tracking
        self._custom_toolset_paths = custom_toolset_paths

        if toolset_status_location is None:
            toolset_status_location = FilePath(DEFAULT_TOOLSET_STATUS_LOCATION)
        self.toolset_status_location = toolset_status_location

    @property
    def cli_tool_tags(self) -> List[ToolsetTag]:
        """
        .. deprecated::
            Use explicit ``[ToolsetTag.CORE, ToolsetTag.CLI]`` instead.
        """
        return [ToolsetTag.CORE, ToolsetTag.CLI]

    @property
    def server_tool_tags(self) -> List[ToolsetTag]:
        """
        .. deprecated::
            Use explicit ``[ToolsetTag.CORE, ToolsetTag.CLUSTER]`` instead.
        """
        return [ToolsetTag.CORE, ToolsetTag.CLUSTER]

    # ------------------------------------------------------------------
    # Backwards-compatible accessors for tests that set these directly
    # ------------------------------------------------------------------

    @property
    def toolsets(self) -> dict[str, dict[str, Any]]:
        return self.registry.toolsets_config

    @toolsets.setter
    def toolsets(self, value: dict[str, dict[str, Any]]):
        self.registry.toolsets_config = value

    @property
    def custom_toolsets(self) -> Optional[List[FilePath]]:
        return self.registry.custom_toolset_paths or None

    @custom_toolsets.setter
    def custom_toolsets(self, value: Optional[List[FilePath]]):
        self.registry.custom_toolset_paths = value or []

    @property
    def additional_toolsets(self) -> List[Toolset]:
        return self.registry.additional_toolsets

    @additional_toolsets.setter
    def additional_toolsets(self, value: List[Toolset]):
        self.registry.additional_toolsets = value

    @property
    def custom_runbook_catalogs(self) -> Optional[List[Union[str, FilePath]]]:
        return self.registry.custom_runbook_catalogs

    @custom_runbook_catalogs.setter
    def custom_runbook_catalogs(self, value: Optional[List[Union[str, FilePath]]]):
        self.registry.custom_runbook_catalogs = value

    def load_custom_toolsets(self, builtin_toolsets_names: list[str]) -> list[Toolset]:
        """
        .. deprecated::
            Loading logic has moved to :class:`ToolsetRegistry`.
        """
        if not self.registry.custom_toolset_paths and not self.custom_toolsets_from_cli:
            logging.debug(
                "No custom toolsets configured, skipping loading custom toolsets"
            )
            return []
        return self.registry._load_toolsets_from_paths(
            self.registry.custom_toolset_paths, builtin_toolsets_names
        )

    @staticmethod
    def add_or_merge_onto_toolsets(
        self_or_new_toolsets,
        new_toolsets_or_existing,
        existing_toolsets_by_name=None,
    ) -> None:
        """
        .. deprecated::
            Use :func:`toolset_registry._merge_onto` instead.

        Supports both old calling conventions:
        - ToolsetManager.add_or_merge_onto_toolsets(manager, new, existing)
        - manager.add_or_merge_onto_toolsets(new, existing)
        """
        if existing_toolsets_by_name is not None:
            # Called as: add_or_merge_onto_toolsets(self, new_toolsets, existing)
            _merge_onto(new_toolsets_or_existing, existing_toolsets_by_name)
        else:
            # Called as: add_or_merge_onto_toolsets(new_toolsets, existing)
            _merge_onto(self_or_new_toolsets, new_toolsets_or_existing)

    def _list_all_toolsets(
        self,
        dal: Optional[SupabaseDal] = None,
        check_prerequisites=True,
        enable_all_toolsets_possible=False,
        toolset_tags: Optional[List[ToolsetTag]] = None,
        silent: bool = False,
    ) -> List[Toolset]:
        """Get all toolsets from registry, optionally check prerequisites."""
        toolsets_by_name = self.registry.get_all_toolsets(
            dal=dal,
            auto_enable=enable_all_toolsets_possible,
            tag_filter=toolset_tags,
        )

        final_toolsets = list(toolsets_by_name.values())

        # check_prerequisites against each enabled toolset
        if not check_prerequisites:
            return final_toolsets

        enabled_toolsets: List[Toolset] = []
        for toolset in toolsets_by_name.values():
            if toolset.enabled:
                enabled_toolsets.append(toolset)
            else:
                toolset.status = ToolsetStatusEnum.DISABLED
        self.check_toolset_prerequisites(enabled_toolsets, silent=silent)

        return final_toolsets

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare_toolsets(
        self,
        dal: Optional[SupabaseDal] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
        enable_all_toolsets_possible: bool = False,
        prerequisite_cache: PrerequisiteCacheMode = PrerequisiteCacheMode.ENABLED,
    ) -> List[Toolset]:
        """Get toolsets from registry and prepare them for use.

        Args:
            dal: Optional database access layer.
            toolset_tag_filter: Only include toolsets whose tags overlap with this list.
            enable_all_toolsets_possible: If True, automatically enable every toolset that can
                work without explicit configuration.
            prerequisite_cache: Controls prerequisite check caching behavior.
                DISABLED — run full checks eagerly, no disk caching.
                ENABLED — use cached results when available (default).
                FORCE_REFRESH — re-run all checks and update the cache.
        """
        if prerequisite_cache == PrerequisiteCacheMode.DISABLED:
            return self._list_all_toolsets(
                dal,
                check_prerequisites=True,
                enable_all_toolsets_possible=enable_all_toolsets_possible,
                toolset_tags=toolset_tag_filter,
            )
        else:
            return self.load_toolset_with_status(
                dal,
                refresh_status=(prerequisite_cache == PrerequisiteCacheMode.FORCE_REFRESH),
                enable_all_toolsets_possible=enable_all_toolsets_possible,
                toolset_tags=toolset_tag_filter,
            )

    def list_toolsets(
        self,
        dal: Optional[SupabaseDal] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
        enable_all_toolsets_possible: bool = False,
        prerequisite_cache: PrerequisiteCacheMode = PrerequisiteCacheMode.ENABLED,
    ) -> List[Toolset]:
        """
        .. deprecated::
            Use :meth:`prepare_toolsets` instead.
        """
        return self.prepare_toolsets(
            dal=dal,
            toolset_tag_filter=toolset_tag_filter,
            enable_all_toolsets_possible=enable_all_toolsets_possible,
            prerequisite_cache=prerequisite_cache,
        )

    def list_console_toolsets(
        self, dal: Optional[SupabaseDal] = None, refresh_status=False
    ) -> List[Toolset]:
        """
        .. deprecated::
            Use :meth:`prepare_toolsets` with explicit parameters instead.
        """
        return self.prepare_toolsets(
            dal,
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            prerequisite_cache=PrerequisiteCacheMode.FORCE_REFRESH if refresh_status else PrerequisiteCacheMode.ENABLED,
        )

    def list_server_toolsets(
        self, dal: Optional[SupabaseDal] = None, refresh_status=True
    ) -> List[Toolset]:
        """
        .. deprecated::
            Use :meth:`prepare_toolsets` with explicit parameters instead.
        """
        return self.prepare_toolsets(
            dal,
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
            enable_all_toolsets_possible=False,
            prerequisite_cache=PrerequisiteCacheMode.DISABLED,
        )

    def refresh_toolsets_and_get_changes(
        self,
        current_toolsets: List[Toolset],
        dal: Optional[SupabaseDal] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
        enable_all_toolsets_possible: bool = False,
    ) -> tuple[List[Toolset], List[tuple[str, ToolsetStatusEnum, ToolsetStatusEnum]]]:
        old_status_by_name: dict[str, ToolsetStatusEnum] = {
            toolset.name: toolset.status for toolset in current_toolsets
        }

        new_toolsets = self._list_all_toolsets(
            dal,
            check_prerequisites=True,
            enable_all_toolsets_possible=enable_all_toolsets_possible,
            toolset_tags=toolset_tag_filter,
            silent=True,
        )

        new_status_by_name: dict[str, ToolsetStatusEnum] = {
            toolset.name: toolset.status for toolset in new_toolsets
        }

        changes: List[tuple[str, ToolsetStatusEnum, ToolsetStatusEnum]] = []

        # Status transitions for toolsets present in both old and new
        for toolset in new_toolsets:
            old_status = old_status_by_name.get(toolset.name)
            if old_status is not None and old_status != toolset.status:
                changes.append((toolset.name, old_status, toolset.status))

        # Newly added toolsets (in new but not in old)
        for name, status in new_status_by_name.items():
            if name not in old_status_by_name:
                changes.append((name, ToolsetStatusEnum.DISABLED, status))

        # Removed toolsets (in old but not in new)
        for name, status in old_status_by_name.items():
            if name not in new_status_by_name:
                changes.append((name, status, ToolsetStatusEnum.DISABLED))

        return new_toolsets, changes

    def refresh_server_toolsets_and_get_changes(
        self,
        current_toolsets: List[Toolset],
        dal: Optional[SupabaseDal] = None,
    ) -> tuple[List[Toolset], List[tuple[str, ToolsetStatusEnum, ToolsetStatusEnum]]]:
        """
        .. deprecated::
            Use :meth:`refresh_toolsets_and_get_changes` with explicit parameters instead.
        """
        return self.refresh_toolsets_and_get_changes(
            current_toolsets,
            dal,
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
            enable_all_toolsets_possible=False,
        )

    # ------------------------------------------------------------------
    # Internal: status caching
    # ------------------------------------------------------------------

    def _refresh_toolset_status(
        self,
        dal: Optional[SupabaseDal] = None,
        enable_all_toolsets_possible=False,
        toolset_tags: Optional[List[ToolsetTag]] = None,
    ):
        """Refresh the status of all toolsets and cache to disk."""
        all_toolsets = self._list_all_toolsets(
            dal=dal,
            check_prerequisites=True,
            enable_all_toolsets_possible=enable_all_toolsets_possible,
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

    # Keep old name as alias for callers
    refresh_toolset_status = _refresh_toolset_status

    def _get_datasource_file_paths(self) -> list[str]:
        """Collect all datasource config file paths for hash tracking."""
        paths: list[str] = []
        if self.config_file_path:
            paths.append(str(self.config_file_path))
        for p in self._custom_toolset_paths:
            paths.append(str(p))
        return paths

    def _load_toolset_with_status(
        self,
        dal: Optional[SupabaseDal] = None,
        refresh_status: bool = False,
        enable_all_toolsets_possible=False,
        toolset_tags: Optional[List[ToolsetTag]] = None,
    ) -> List[Toolset]:
        """Load toolsets with status from cache, refreshing if needed."""
        # Check if any datasource config file has changed since the last run.
        if not refresh_status:
            datasource_paths = self._get_datasource_file_paths()
            if datasource_paths and check_and_update_config_hashes(datasource_paths):
                logging.info("Datasource config file(s) changed, refreshing toolsets")
                refresh_status = True

        if not os.path.exists(self.toolset_status_location) or refresh_status:
            logging.info("Refreshing available datasources (toolsets)")
            self.refresh_toolset_status(
                dal, enable_all_toolsets_possible=enable_all_toolsets_possible, toolset_tags=toolset_tags
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
            # check prerequisites for only enabled toolset when the toolset is loaded from cache
            if toolset.enabled and (
                toolset.status == ToolsetStatusEnum.ENABLED
                or toolset.type == ToolsetType.MCP
            ):
                enabled_toolsets_from_cache.append(toolset)

        if using_cached:
            # Lazy initialization: only run fast config-validity checks on startup
            lazy_toolsets: List[Toolset] = []
            eager_toolsets: List[Toolset] = []
            for toolset in enabled_toolsets_from_cache:
                if toolset.type == ToolsetType.MCP:
                    eager_toolsets.append(toolset)
                else:
                    lazy_toolsets.append(toolset)

            self._check_config_prerequisites(lazy_toolsets)
            if eager_toolsets:
                self.check_toolset_prerequisites(eager_toolsets)
        else:
            self.check_toolset_prerequisites(enabled_toolsets_from_cache)

        # CLI custom toolsets status are not cached
        custom_toolsets_from_cli = self.registry._load_toolsets_from_paths(
            self.custom_toolsets_from_cli or [],
            list(toolsets_status_by_name.keys()),
            check_conflict_default=True,
        )

        # custom toolsets from cli should not override custom toolsets from config
        enabled_toolsets_from_cli: List[Toolset] = []
        for custom_toolset_from_cli in custom_toolsets_from_cli:
            if custom_toolset_from_cli.name in toolsets_status_by_name:
                raise ValueError(
                    f"Toolset {custom_toolset_from_cli.name} from cli is already defined in existing toolset"
                )
            enabled_toolsets_from_cli.append(custom_toolset_from_cli)
        self.check_toolset_prerequisites(enabled_toolsets_from_cli)

        all_toolsets_with_status.extend(custom_toolsets_from_cli)

        # Additional Python toolsets passed programmatically are not cached
        if self.registry.additional_toolsets:
            already_checked_names = {ts.name for ts in enabled_toolsets_from_cache} | {
                ts.name for ts in enabled_toolsets_from_cli
            }
            additional_to_check = [
                ts
                for ts in all_toolsets_with_status
                if ts.name
                in {ats.name for ats in self.registry.additional_toolsets}
                and ts.enabled
                and ts.name not in already_checked_names
            ]
            if additional_to_check:
                self.check_toolset_prerequisites(additional_to_check)

        if using_cached:
            num_available_toolsets = len(
                [toolset for toolset in all_toolsets_with_status if toolset.enabled]
            )
            logging.info(
                f"Using {num_available_toolsets} datasources (toolsets). To refresh: use flag `--refresh-toolsets`"
            )
        return all_toolsets_with_status

    # Keep old name as alias
    load_toolset_with_status = _load_toolset_with_status

    # ------------------------------------------------------------------
    # Prerequisite checking
    # ------------------------------------------------------------------

    @classmethod
    def check_toolset_prerequisites(cls, toolsets: list[Toolset], silent: bool = False):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for toolset in toolsets:
                futures.append(executor.submit(toolset.check_prerequisites, silent))

            for _ in concurrent.futures.as_completed(futures):
                pass

    @staticmethod
    def _check_config_prerequisites(toolsets: list[Toolset]) -> None:
        """Run only fast config-validity checks for lazy-loaded toolsets."""
        for toolset in toolsets:
            toolset.check_config_prerequisites()

    # ------------------------------------------------------------------
