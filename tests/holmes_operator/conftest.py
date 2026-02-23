"""Pytest fixtures for operator tests."""

import pytest
import respx


@pytest.fixture
def respx_mock():
    """Provide respx mock for httpx calls."""
    with respx.mock:
        yield respx
