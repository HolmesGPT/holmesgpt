import logging
import os
from typing import Any, ClassVar, Tuple, Type

from holmes.core.tools import CallablePrerequisite, Toolset, ToolsetTag
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.dbdash.common import DBADashClient, DBADashConfig
from holmes.plugins.toolsets.dbdash.tools.alerts import GetActiveAlerts, GetClosedAlerts
from holmes.plugins.toolsets.dbdash.tools.instances import (
    GetInstanceDetails,
    ListInstances,
)
from holmes.plugins.toolsets.dbdash.tools.performance import (
    GetCpuMetrics,
    GetIoStats,
    GetMemoryMetrics,
    GetWaitStats,
)
from holmes.plugins.toolsets.dbdash.tools.queries import (
    GetBlockingQueries,
    GetQueryStoreTop,
    GetRunningQueries,
    GetSlowQueries,
)

logger = logging.getLogger(__name__)


class DBADashToolset(Toolset):
    """Toolset for investigating SQL Server issues via DBADash Web."""

    config_classes: ClassVar[list[Type[DBADashConfig]]] = [DBADashConfig]

    def __init__(self, name: str = "dbdash"):
        super().__init__(
            name=name,
            description="Investigate SQL Server database performance issues and alerts via DBADash Web (db dash). Use dbdash tools for any question about SQL Server databases, DB instances, or DBADash.",
            icon_url=None,
            docs_url=None,
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListInstances(self),
                GetInstanceDetails(self),
                GetActiveAlerts(self),
                GetClosedAlerts(self),
                GetCpuMetrics(self),
                GetMemoryMetrics(self),
                GetWaitStats(self),
                GetIoStats(self),
                GetSlowQueries(self),
                GetRunningQueries(self),
                GetBlockingQueries(self),
                GetQueryStoreTop(self),
            ],
            tags=[ToolsetTag.CORE],
            enabled=False,
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    @property
    def dbdash_config(self) -> DBADashConfig:
        if not hasattr(self, "_dbdash_config") or self._dbdash_config is None:
            raise RuntimeError("DBADash toolset not initialized — config is None")
        return self._dbdash_config

    @property
    def client(self) -> DBADashClient:
        if not hasattr(self, "_dbdash_client") or self._dbdash_client is None:
            raise RuntimeError("DBADash toolset not initialized — client is None")
        return self._dbdash_client

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            logger.debug("DBADash config not provided for %s", self.name)
            return False, TOOLSET_CONFIG_MISSING_ERROR

        try:
            self._dbdash_config = DBADashConfig(**config)
            self._dbdash_client = DBADashClient(self._dbdash_config)
            self._dbdash_client.health_check()
            return True, ""
        except Exception as e:
            logger.exception("Failed to set up DBADash toolset %s", self.name)
            return False, str(e)
