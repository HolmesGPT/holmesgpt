"""conftest for conversation worker integration tests.

These tests talk to the real Robusta platform + Supabase (and the
retry-resilience test builds a real in-process SupabaseDal via ``import
server``). Override the unit-test autouse fixtures from the root conftest so
they don't mock out the DAL / HTTP layer for this directory.
"""
import pytest
import responses as responses_

# Re-export the session-scoped fixture so all test modules can use it.
from tests.core.conversations_worker.integration import supabase_fx  # noqa: F401


@pytest.fixture(autouse=True, scope="session")
def storage_dal_mock():
    """Override root: do NOT patch holmes.config.SupabaseDal — these tests need
    the real DAL talking to the real Supabase backend."""
    yield None


@pytest.fixture(autouse=True)
def patch_supabase():
    """Override root: do NOT swap in fake Supabase connection settings."""
    yield


@pytest.fixture(autouse=True)
def responses():
    """Override root: let all HTTP through to the real services."""
    with responses_.RequestsMock(passthru_prefixes=("http://", "https://")) as rsps:
        yield rsps
