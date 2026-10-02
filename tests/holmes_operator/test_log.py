import ast
import json
import logging
from io import StringIO
from pathlib import Path

import holmes_operator
from holmes_operator.log import (
    JSON_LOG_DATEFMT,
    JSON_LOG_FMT,
    JSON_LOG_RENAME_FIELDS,
    build_json_formatter,
)


def test_build_json_formatter_emits_valid_json():
    buffer = StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(build_json_formatter())

    logger = logging.getLogger("holmes_operator.test.json")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.handlers = [handler]

    logger.info("hello json")

    line = buffer.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "hello json"
    assert payload["severity"] == "INFO"
    assert payload["name"] == "holmes_operator.test.json"


def test_json_format_constants_match_server():
    from holmes.utils import log as server_log

    assert JSON_LOG_FMT == server_log.JSON_LOG_FMT
    assert JSON_LOG_DATEFMT == server_log.JSON_LOG_DATEFMT
    assert JSON_LOG_RENAME_FIELDS == server_log.JSON_LOG_RENAME_FIELDS


def test_holmes_operator_does_not_import_holmes_package():
    package_dir = Path(holmes_operator.__file__).parent

    offenders = []
    for py_file in package_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module and node.level == 0 else []
            else:
                continue
            for name in names:
                if name == "holmes" or name.startswith("holmes."):
                    offenders.append(f"{py_file.relative_to(package_dir)}:{node.lineno} imports {name}")

    assert not offenders, (
        "holmes_operator must not import the holmes package (it is not present "
        "in the operator image):\n" + "\n".join(offenders)
    )
