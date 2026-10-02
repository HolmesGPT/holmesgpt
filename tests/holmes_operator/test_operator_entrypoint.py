import subprocess
import sys
from pathlib import Path

import holmes_operator

REPO_ROOT = Path(holmes_operator.__file__).parent.parent

BOOTSTRAP = """
import importlib, importlib.abc, sys

class BlockHolmesPackage(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "holmes" or name.startswith("holmes."):
            raise ModuleNotFoundError(f"No module named '{name}'")

sys.meta_path.insert(0, BlockHolmesPackage())
importlib.import_module("holmes_operator.operator")
print("entrypoint-ok")
"""


def test_operator_entrypoint_starts_without_holmes_package():
    result = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        "The operator entrypoint failed to load without the holmes package — "
        "this would crashloop the holmes-operator image at startup "
        f"(see issue #2336):\n{result.stderr}"
    )
    assert "entrypoint-ok" in result.stdout
