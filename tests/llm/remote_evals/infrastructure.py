"""Infrastructure manager for remote evals with reference counting.

Manages test infrastructure lifecycle with two modes:
1. Setup once: All infrastructure setup at server start (parallel)
2. Per-run: Setup/cleanup per eval with reference counting (on-demand)
"""

import asyncio
from collections import defaultdict
from typing import Dict, Set

from tests.llm.utils.setup_cleanup import Operation, run_all_test_commands, run_commands
from tests.llm.utils.test_case_utils import HolmesTestCase


class InfrastructureManager:
    """Manages test infrastructure lifecycle with reference counting for concurrent access."""

    def __init__(self):
        # Track active eval count per test
        self._active_evals: Dict[str, int] = defaultdict(int)

        # Lock per test for setup/cleanup operations
        self._locks: Dict[str, asyncio.Lock] = {}

        # Track setup status per test
        self._setup_complete: Set[str] = set()

        # Global flag to prevent new setups during mass cleanup
        self._shutting_down = False

    def _get_lock(self, test_id: str) -> asyncio.Lock:
        """Get or create lock for test_id."""
        if test_id not in self._locks:
            self._locks[test_id] = asyncio.Lock()
        return self._locks[test_id]

    async def acquire_infrastructure(self, test_case: HolmesTestCase) -> None:
        """Acquire infrastructure for a test (setup if needed, increment ref count).

        Args:
            test_case: Test case requiring infrastructure

        Raises:
            RuntimeError: If server is shutting down
        """
        if self._shutting_down:
            raise RuntimeError("Server is shutting down, cannot start new evals")

        lock = self._get_lock(test_case.id)
        async with lock:
            # Setup if not already done
            if test_case.id not in self._setup_complete:
                print(f"🔧 Setting up infrastructure for {test_case.id}")
                await asyncio.to_thread(
                    run_commands, test_case, test_case.before_test, "setup"
                )
                self._setup_complete.add(test_case.id)
                print(f"✅ Setup complete for {test_case.id}")
            else:
                print(f"♻️  Reusing existing infrastructure for {test_case.id}")

            # Increment reference count
            self._active_evals[test_case.id] += 1
            print(
                f"📊 Active evals for {test_case.id}: {self._active_evals[test_case.id]}"
            )

    async def release_infrastructure(self, test_case: HolmesTestCase) -> None:
        """Release infrastructure for a test (decrement ref count, cleanup if zero).

        Args:
            test_case: Test case releasing infrastructure
        """
        lock = self._get_lock(test_case.id)
        async with lock:
            # Decrement reference count
            self._active_evals[test_case.id] -= 1
            print(
                f"📊 Active evals for {test_case.id}: {self._active_evals[test_case.id]}"
            )

            # Cleanup if no more active evals
            if self._active_evals[test_case.id] == 0:
                print(f"🧹 Cleaning up infrastructure for {test_case.id}")
                await asyncio.to_thread(
                    run_commands, test_case, test_case.after_test, "cleanup"
                )
                self._setup_complete.discard(test_case.id)
                print(f"✅ Cleanup complete for {test_case.id}")

    async def setup_all_once(self, test_cases: list[HolmesTestCase]) -> None:
        """Setup all infrastructure at server start (parallel).

        Args:
            test_cases: List of all test cases to setup
        """
        print(f"🚀 Setting up infrastructure for {len(test_cases)} tests")
        await asyncio.to_thread(run_all_test_commands, test_cases, Operation.SETUP)
        # Mark all as setup
        for tc in test_cases:
            self._setup_complete.add(tc.id)
        print("✅ All infrastructure ready")

    async def cleanup_all(self, test_cases: list[HolmesTestCase]) -> None:
        """Cleanup all infrastructure at server shutdown.

        Waits for all active evals to complete before cleaning up.

        Args:
            test_cases: List of all test cases to cleanup
        """
        self._shutting_down = True

        # Wait for all active evals to finish
        while any(count > 0 for count in self._active_evals.values()):
            active = {
                tid: count for tid, count in self._active_evals.items() if count > 0
            }
            print(
                f"⏳ Waiting for {len(active)} active evals to complete: {list(active.keys())}"
            )
            await asyncio.sleep(1)

        print(f"🧹 Cleaning up infrastructure for {len(test_cases)} tests")
        await asyncio.to_thread(run_all_test_commands, test_cases, Operation.CLEANUP)
        print("✅ All infrastructure cleaned up")


# Global singleton
infrastructure_manager = InfrastructureManager()
