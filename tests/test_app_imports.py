import os

import pytest

EXPECTED_LINES = [
    "# ruff: noqa: E402\n",
    "import os\n",
    "\n",
    "from holmes.utils.cert_utils import add_custom_certificate\n",
    "\n",
    'ADDITIONAL_CERTIFICATE: str = os.environ.get("CERTIFICATE", "")\n',
    "if add_custom_certificate(ADDITIONAL_CERTIFICATE):\n",
    '    print("added custom certificate")\n',
    "\n",
    "# DO NOT ADD ANY IMPORTS OR CODE ABOVE THIS LINE\n",
    "# IMPORTING ABOVE MIGHT INITIALIZE AN HTTPS CLIENT THAT DOESN'T TRUST THE CUSTOM CERTIFICATE\n",
]


@pytest.mark.parametrize(
    "file_path,file_name",
    [
        ("holmes/main.py", "main.py"),
        ("server.py", "server.py"),
        ("experimental/ag-ui/server-agui.py", "server-agui.py"),
    ],
)
def test_app_files_have_correct_initial_lines(file_path, file_name):
    """Test that app files start with the required certificate handling code."""
    full_path = os.path.join(os.path.dirname(__file__), "..", file_path)

    with open(full_path, "r") as f:
        lines = f.readlines()

    for i, expected_line in enumerate(EXPECTED_LINES):
        assert (
            lines[i] == expected_line
        ), f"Line {i + 1} should be: {expected_line.strip()!r}, but got: {lines[i].strip()!r}. This tests make sure the import order in {file_name} file is correct, if you see this, go to {file_name} file and move your imports code to lower lines."
