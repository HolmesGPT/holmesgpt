"""Pytest fixtures for frontend tests.

Adds the top-level `frontend/` directory to sys.path so that `server_frontend`
and `projects` (which live in `frontend/`) can be imported directly by tests.
"""

import os
import sys

_FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
)
if _FRONTEND_DIR not in sys.path:
    sys.path.insert(0, _FRONTEND_DIR)
