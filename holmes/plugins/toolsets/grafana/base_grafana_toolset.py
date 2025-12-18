import logging
from abc import abstractmethod
from typing import Any, ClassVar, Tuple, Type

from holmes.core.tools import CallablePrerequisite, Tool, Toolset, ToolsetTag
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.grafana.common import GrafanaConfig


class BaseGrafanaToolset(Toolset):
    config_class: ClassVar[Type[GrafanaConfig]] = GrafanaConfig

    def __init__(
        self,
        name: str,
        description: str,
        icon_url: str,
        tools: list[Tool],
        docs_url: str,
    ):
        super().__init__(
            name=name,
            description=description,
            icon_url=icon_url,
            docs_url=docs_url,
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=tools,
            tags=[
                ToolsetTag.CORE,
            ],
            enabled=False,
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            logging.debug(f"Grafana config not provided {self.name}")
            return False, TOOLSET_CONFIG_MISSING_ERROR

        try:
            self._grafana_config = self.config_class(**config)
            return self.health_check()

        except Exception as e:
            logging.exception(f"Failed to set up grafana toolset {self.name}")
            return False, str(e)

    @abstractmethod
    def health_check(self) -> Tuple[bool, str]:
        """
        Check if the toolset is healthy and can connect to its data source.

        Subclasses must implement this method to verify connectivity.
        This method should NOT raise exceptions - catch them internally
        and return (False, "error message") instead.

        Returns:
            Tuple[bool, str]: (True, "") on success, (False, "error message") on failure.
        """
        raise NotImplementedError("Subclasses must implement health_check()")

    def get_example_config(self):
        example_config = GrafanaConfig(
            api_key="YOUR API KEY",
            url="YOUR GRAFANA URL",
            grafana_datasource_uid="UID OF DATASOURCE IN GRAFANA",
        )
        return example_config.model_dump()
