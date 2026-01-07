"""Entry point for remote eval server.

This module handles server startup, infrastructure setup, and cleanup.

Usage:
    # Setup all infrastructure once at server start (default)
    braintrust eval tests/llm/remote_evals --dev

    # On-demand setup per eval (slower, isolated)
    SETUP_MODE=per_run braintrust eval tests/llm/remote_evals --dev

    # Custom port
    braintrust eval tests/llm/remote_evals --dev --dev-port 8301
"""

import asyncio
import os
import signal
import sys
from pathlib import Path

# Import server module to register all evals
# This MUST be imported to register the Eval() definitions
from tests.llm.remote_evals import server  # noqa: F401
from tests.llm.remote_evals.infrastructure import infrastructure_manager
from tests.llm.utils.test_case_utils import get_test_cases

TEST_CASES_FOLDER = Path(__file__).parent.parent / "fixtures" / "test_ask_holmes"


async def main():
    """Main entry point for remote eval server."""
    setup_mode = os.environ.get("SETUP_MODE", "once")

    if setup_mode == "once":
        print("🚀 Starting remote eval server with 'setup once' mode")
        test_cases = get_test_cases(TEST_CASES_FOLDER)

        # Setup all infrastructure upfront
        try:
            await infrastructure_manager.setup_all_once(test_cases)
        except Exception as e:
            print(f"❌ Failed to setup infrastructure: {e}")
            print("⚠️  Server startup aborted")
            sys.exit(1)

        # Register cleanup on shutdown
        def cleanup_handler(signum, frame):
            """Handle shutdown signals gracefully."""
            print("\n🛑 Shutting down server...")
            # Create cleanup task
            loop = asyncio.get_event_loop()
            loop.create_task(infrastructure_manager.cleanup_all(test_cases))
            # Give cleanup 30 seconds to complete
            loop.call_later(30, lambda: sys.exit(0))

        signal.signal(signal.SIGINT, cleanup_handler)
        signal.signal(signal.SIGTERM, cleanup_handler)

        print(f"✅ Remote eval server ready with {len(test_cases)} evals")
        print("🌐 Server running at: http://localhost:8300 (default)")
        print("💡 Configure in Braintrust: Settings > Remote Evals > Add Source")
        print("⚠️  Note: In 'setup once' mode, avoid running multiple evals simultaneously")
    else:
        print("🚀 Starting remote eval server with 'per_run' mode")
        print("⚙️  Infrastructure will be setup/cleanup for each eval run")
        print("✅ Remote eval server ready")
        print("🌐 Server running at: http://localhost:8300 (default)")
        print("💡 Configure in Braintrust: Settings > Remote Evals > Add Source")
        print("✨ Per-run mode supports concurrent eval execution")


if __name__ == "__main__":
    # Check if we're actually running the dev server
    # (not just importing the module)
    if "--dev" in sys.argv or os.environ.get("BRAINTRUST_DEV_MODE"):
        asyncio.run(main())
    else:
        # Just import server to register evals
        # The actual server is started by braintrust CLI
        print("ℹ️  Remote evals registered. Use 'braintrust eval --dev' to start server.")
