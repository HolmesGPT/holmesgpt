import logging
import warnings
from enum import Enum
from typing import List, Optional

from rich.console import Console
from rich.logging import RichHandler


class Verbosity(Enum):
    NORMAL = 0
    LOG_QUERIES = 1  # TODO: currently unused
    VERBOSE = 2
    VERY_VERBOSE = 3


def cli_flags_to_verbosity(verbose_flags: List[bool]) -> Verbosity:
    if verbose_flags is None or len(verbose_flags) == 0:
        return Verbosity.NORMAL
    elif len(verbose_flags) == 1:
        return Verbosity.LOG_QUERIES
    elif len(verbose_flags) == 2:
        return Verbosity.VERBOSE
    else:
        return Verbosity.VERY_VERBOSE


def suppress_noisy_logs():
    # disable INFO logs from OpenAI
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # disable INFO logs from LiteLLM
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    # disable INFO logs from AWS (relevant when using bedrock)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    # when running in --verbose mode we don't want to see DEBUG logs from these libraries
    logging.getLogger("openai._base_client").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("markdown_it").setLevel(logging.INFO)
    # suppress UserWarnings from the slack_sdk module
    warnings.filterwarnings("ignore", category=UserWarning, module="slack_sdk.*")


def init_logging(verbose_flags: Optional[List[bool]] = None, log_costs: bool = False):
    verbosity = cli_flags_to_verbosity(verbose_flags)  # type: ignore

    # Setup cost logger if requested
    if log_costs:
        cost_logger = logging.getLogger("holmes.costs")
        cost_logger.setLevel(logging.DEBUG)

    if verbosity == Verbosity.VERY_VERBOSE:
        logging.basicConfig(
            force=True,
            level=logging.DEBUG,
            format="%(message)s",
            handlers=[
                RichHandler(
                    show_level=False,
                    markup=True,
                    show_time=False,
                    show_path=False,
                    console=Console(width=None),
                )
            ],
        )
    elif verbosity == Verbosity.VERBOSE:
        logging.basicConfig(
            force=True,
            level=logging.INFO,
            format="%(message)s",
            handlers=[
                RichHandler(
                    show_level=False,
                    markup=True,
                    show_time=False,
                    show_path=False,
                    console=Console(width=None),
                )
            ],
        )
        logging.getLogger().setLevel(logging.DEBUG)
        suppress_noisy_logs()
    else:
        logging.basicConfig(
            force=True,
            level=logging.INFO,
            format="%(message)s",
            handlers=[
                RichHandler(
                    show_level=False,
                    markup=True,
                    show_time=False,
                    show_path=False,
                    console=Console(width=None),
                )
            ],
        )
        suppress_noisy_logs()

    logging.debug(f"verbosity is {verbosity}")

    return Console()
