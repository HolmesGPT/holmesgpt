from subprocess import CompletedProcess
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_run():
    """Expose a single container for tests with one shared log response."""
    with patch("subprocess.run") as run:

        def kubectl(cmd, **kwargs):
            if cmd[:3] == ["kubectl", "get", "pod"]:
                return CompletedProcess(
                    cmd, 0, '{"spec": {"containers": [{"name": "app"}]}}', ""
                )
            return run.return_value

        run.side_effect = kubectl
        yield run
