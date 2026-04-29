"""conftest for conversation worker integration tests."""
# Re-export the session-scoped fixture so all test modules can use it.
from tests.core.conversations_worker.integration import supabase_fx  # noqa: F401
