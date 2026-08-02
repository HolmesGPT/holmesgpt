"""Start-up test for the operator entrypoint, simulating the operator image.

The operator image (Dockerfile.operator) ships only the holmes_operator
package — the holmes package does not exist there. 0.37.0 shipped an operator
that crashlooped on `ModuleNotFoundError: No module named 'holmes'` raised at
module load time, before kopf even started (issue #2336).

This test launches the entrypoint module in a subprocess where the holmes
package is blocked, which reproduces the image environment: everything the
operator executes at startup up to kopf.run() must succeed without holmes.
A subprocess is used (rather than importing in-process) both to apply the
import blocker cleanly and to avoid polluting the test process with the
entrypoint's logging.basicConfig(force=True) and kopf handler registration.
"""

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
