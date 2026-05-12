import logging
from abc import abstractmethod
from typing import Any, ClassVar, Optional, Tuple, Type

from pydantic import ValidationError

from holmes.core.tools import CallablePrerequisite, Tool, Toolset, ToolsetTag
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.grafana.common import GrafanaConfig


class BaseGrafanaToolset(Toolset):
    config_classes: ClassVar[list[Type[GrafanaConfig]]] = [GrafanaConfig]

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

        config_classes = list(self.config_classes or [GrafanaConfig])
        # Try each config class in order and use the first one that validates.
        # This supports toolsets with multiple variant configs (e.g. Loki via Grafana
        # proxy vs direct vs Grafana Cloud) — whichever variant matches the
        # user-supplied fields wins. The first class listed acts as the preferred
        # variant when multiple could match.
        last_error: Optional[Exception] = None
        for config_class in config_classes:
            try:
                self._grafana_config = config_class(**config)
                return self.health_check()
            except ValidationError as e:
                last_error = e
                logging.debug(
                    f"Config {config_class.__name__} did not validate for {self.name}: {e}"
                )
                continue
            except Exception as e:
                logging.exception(f"Failed to set up grafana toolset {self.name}")
                return False, f"Failed to set up {self.name}: {e}"

        logging.warning(
            f"No config class matched for {self.name}. Tried: "
            f"{[c.__name__ for c in config_classes]}"
        )
        if last_error:
            return False, f"Invalid {self.name} configuration: {last_error}"
        return (
            False,
            "No config variant matched the provided fields — check the docs for required fields per variant.",
        )

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
