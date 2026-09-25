"""
TDD Red Tests for SupabaseDal Content Size Limits

These tests expose bugs where content fetched from SupabaseDal has no size limits,
potentially causing context window overflow.

Key issues:
1. get_global_instructions_for_account() returns unbounded instructions
2. get_runbook_content() returns unbounded runbook instructions
3. get_runbook_catalog() returns unbounded catalog entries
4. get_resource_instructions() returns unbounded instructions
"""

from unittest.mock import MagicMock, patch

from holmes.core.supabase_dal import SupabaseDal
from holmes.utils.global_instructions import Instructions


class TestDalGlobalInstructionsLimits:
    """Tests for global instructions size limits in DAL."""

    def test_global_instructions_should_have_size_limit(self):
        """
        Global instructions fetched from Supabase should be limited in size
        to prevent context window overflow.

        The error "prompt is too long: 214764 tokens > 200000 maximum"
        can occur when global_instructions are very large.
        """
        # Create a mock SupabaseDal
        with patch.object(SupabaseDal, "__init__", lambda self, cluster: None):
            dal = SupabaseDal("test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"

            # Mock the Supabase client to return huge instructions
            mock_client = MagicMock()
            large_instructions = ["Check system " + str(i) + ". " * 100 for i in range(1000)]

            mock_response = MagicMock()
            mock_response.data = [{"runbook": {"instructions": large_instructions}}]

            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
            dal.client = mock_client

            # Fetch global instructions
            result = dal.get_global_instructions_for_account()

            assert result is not None

            # Calculate total size
            total_chars = sum(len(instr) for instr in result.instructions)

            # Global instructions should be bounded
            MAX_GLOBAL_INSTRUCTIONS_CHARS = 50000

            assert total_chars <= MAX_GLOBAL_INSTRUCTIONS_CHARS, (
                f"Global instructions too large: {total_chars} chars. "
                f"This can cause context window overflow."
            )

    def test_individual_instruction_should_be_validated(self):
        """
        Individual instruction items should be validated and potentially
        truncated if too large.
        """
        with patch.object(SupabaseDal, "__init__", lambda self, cluster: None):
            dal = SupabaseDal("test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"

            mock_client = MagicMock()
            # Single instruction that's extremely large (100KB)
            huge_instruction = "A" * 100000

            mock_response = MagicMock()
            mock_response.data = [{"runbook": {"instructions": [huge_instruction]}}]

            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
            dal.client = mock_client

            result = dal.get_global_instructions_for_account()

            assert result is not None
            assert len(result.instructions) == 1

            # Individual instructions should be bounded
            MAX_SINGLE_INSTRUCTION_CHARS = 10000

            assert len(result.instructions[0]) <= MAX_SINGLE_INSTRUCTION_CHARS, (
                f"Single instruction too large: {len(result.instructions[0])} chars. "
                f"Individual instructions should be validated/truncated."
            )


class TestDalRunbookContentLimits:
    """Tests for runbook content size limits in DAL."""

    def test_runbook_content_should_have_size_limit(self):
        """
        Runbook content (instruction field) fetched from Supabase should
        be limited to prevent context overflow when returned as tool result.
        """
        with patch.object(SupabaseDal, "__init__", lambda self, cluster: None):
            dal = SupabaseDal("test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"

            mock_client = MagicMock()
            # Create a huge instruction
            huge_instruction = "Step 1: " + "Check logs. " * 20000  # ~200KB

            mock_response = MagicMock()
            mock_response.data = [{
                "runbook_id": "test-id",
                "symptoms": "System slow",
                "subject_name": "Performance Guide",
                "runbook": {"instructions": huge_instruction}
            }]

            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
            dal.client = mock_client

            result = dal.get_runbook_content("test-id")

            assert result is not None
            assert result.instruction is not None

            # Runbook instruction should be bounded
            MAX_RUNBOOK_INSTRUCTION_CHARS = 100000

            assert len(result.instruction) <= MAX_RUNBOOK_INSTRUCTION_CHARS, (
                f"Runbook instruction too large: {len(result.instruction)} chars. "
                f"This is returned via fetch_runbook tool and can cause overflow."
            )


class TestDalRunbookCatalogLimits:
    """Tests for runbook catalog size limits in DAL."""

    def test_runbook_catalog_should_have_entry_limit(self):
        """
        The runbook catalog should have a limit on the number of entries
        to prevent the catalog from overwhelming the user prompt.
        """
        with patch.object(SupabaseDal, "__init__", lambda self, cluster: None):
            dal = SupabaseDal("test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"

            mock_client = MagicMock()
            # Create many catalog entries
            entries = []
            for i in range(500):
                entries.append({
                    "runbook_id": f"runbook-{i:04d}",
                    "symptoms": f"Symptom description for runbook {i} which is quite detailed",
                    "subject_name": f"Troubleshooting Guide {i}",
                })

            mock_response = MagicMock()
            mock_response.data = entries

            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
            dal.client = mock_client

            result = dal.get_runbook_catalog()

            assert result is not None

            # Catalog should have reasonable number of entries
            MAX_CATALOG_ENTRIES = 100

            assert len(result) <= MAX_CATALOG_ENTRIES, (
                f"Runbook catalog has {len(result)} entries. "
                f"Should be limited to prevent prompt overflow."
            )


class TestDalResourceInstructionsLimits:
    """Tests for resource instructions size limits in DAL."""

    def test_resource_instructions_should_have_size_limit(self):
        """
        Resource-specific instructions should be bounded to prevent
        contributing to context overflow.
        """
        with patch.object(SupabaseDal, "__init__", lambda self, cluster: None):
            dal = SupabaseDal("test-cluster")
            dal.enabled = True
            dal.account_id = "test-account"

            mock_client = MagicMock()
            # Large resource instructions
            large_instructions = ["Check component. " * 1000 for _ in range(50)]

            mock_response = MagicMock()
            mock_response.data = [{
                "runbook": {
                    "instructions": large_instructions,
                    "documents": []
                }
            }]

            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = mock_response
            dal.client = mock_client

            result = dal.get_resource_instructions("Deployment", "my-app")

            assert result is not None

            total_chars = sum(len(i) for i in result.instructions)
            MAX_RESOURCE_INSTRUCTIONS_CHARS = 20000

            assert total_chars <= MAX_RESOURCE_INSTRUCTIONS_CHARS, (
                f"Resource instructions too large: {total_chars} chars. "
                f"Should be bounded."
            )
