from unittest.mock import patch
from holmes.core.truncation.dal_truncation_utils import (
    truncate_evidences_entities_if_necessary,
)


class TestTruncateEvidencesEntitiesIfNecessary:
    """Test cases for the truncate_evidences_entities_if_necessary function."""

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_truncate_long_evidence_data(self):
        """Test that evidence data longer than the limit gets truncated."""
        long_data = "a" * 150  # 150 characters, exceeds limit of 100
        evidence_list = [
            {"data": long_data, "id": "test-1"},
            {"data": "short", "id": "test-2"},
        ]

        truncate_evidences_entities_if_necessary(evidence_list)

        # First evidence should be truncated
        expected_truncated = (
            "a" * 100 + "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
        )
        assert evidence_list[0]["data"] == expected_truncated
        # Second evidence should remain unchanged
        assert evidence_list[1]["data"] == "short"

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_no_truncation_when_data_within_limit(self):
        """Test that evidence data within the limit remains unchanged."""
        short_data = "a" * 50  # 50 characters, within limit of 100
        evidence_list = [
            {"data": short_data, "id": "test-1"},
            {"data": "very short", "id": "test-2"},
        ]

        original_data_0 = evidence_list[0]["data"]
        original_data_1 = evidence_list[1]["data"]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Both should remain unchanged
        assert evidence_list[0]["data"] == original_data_0
        assert evidence_list[1]["data"] == original_data_1

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_truncation_at_exact_limit(self):
        """Test behavior when data is exactly at the limit."""
        exact_limit_data = "a" * 100  # Exactly 100 characters
        evidence_list = [{"data": exact_limit_data, "id": "test-1"}]

        original_data = evidence_list[0]["data"]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Should remain unchanged (not greater than limit)
        assert evidence_list[0]["data"] == original_data

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_truncation_one_character_over_limit(self):
        """Test behavior when data is one character over the limit."""
        over_limit_data = "a" * 101  # 101 characters, one over limit of 100
        evidence_list = [{"data": over_limit_data, "id": "test-1"}]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Should be truncated
        expected_truncated = (
            "a" * 100 + "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
        )
        assert evidence_list[0]["data"] == expected_truncated

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        None,
    )
    def test_no_truncation_when_limit_is_none(self):
        """Test that no truncation occurs when MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION is None."""
        long_data = "a" * 10000
        evidence_list = [{"data": long_data, "id": "test-1"}]

        original_data = evidence_list[0]["data"]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Should remain unchanged
        assert evidence_list[0]["data"] == original_data

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        0,
    )
    def test_no_truncation_when_limit_is_zero(self):
        """Test that no truncation occurs when MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION is 0."""
        long_data = "a" * 1000
        evidence_list = [{"data": long_data, "id": "test-1"}]

        original_data = evidence_list[0]["data"]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Should remain unchanged
        assert evidence_list[0]["data"] == original_data

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        -1,
    )
    def test_no_truncation_when_limit_is_negative(self):
        """Test that no truncation occurs when MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION is negative."""
        long_data = "a" * 1000
        evidence_list = [{"data": long_data, "id": "test-1"}]

        original_data = evidence_list[0]["data"]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Should remain unchanged
        assert evidence_list[0]["data"] == original_data

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_empty_evidence_list(self):
        """Test that function handles empty evidence list without errors."""
        evidence_list = []

        truncate_evidences_entities_if_necessary(evidence_list)

        # Should remain empty
        assert evidence_list == []

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_evidence_without_data_field(self):
        """Test that evidence without 'data' field is handled gracefully."""
        evidence_list = [
            {"id": "test-1", "type": "log"},
            {"data": "valid_data", "id": "test-2"},
        ]

        truncate_evidences_entities_if_necessary(evidence_list)

        # First evidence should remain unchanged (no data field)
        assert evidence_list[0] == {"id": "test-1", "type": "log"}
        # Second evidence should remain unchanged (data is short)
        assert evidence_list[1]["data"] == "valid_data"

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_evidence_with_none_data(self):
        """Test that evidence with None data is handled gracefully."""
        evidence_list = [
            {"data": None, "id": "test-1"},
            {"data": "valid_data", "id": "test-2"},
        ]

        truncate_evidences_entities_if_necessary(evidence_list)

        # First evidence should remain unchanged (data is None)
        assert evidence_list[0]["data"] is None
        # Second evidence should remain unchanged (data is short)
        assert evidence_list[1]["data"] == "valid_data"

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_evidence_with_non_string_data(self):
        """Test that evidence with non-string data is converted to string and truncated if needed."""
        large_dict = {
            "key" + str(i): "value" + str(i) for i in range(20)
        }  # Creates a long string representation
        evidence_list = [
            {"data": large_dict, "id": "test-1"},
            {"data": 12345, "id": "test-2"},
        ]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Dict data should be converted to string and potentially truncated
        dict_str = str(large_dict)
        if len(dict_str) > 100:
            expected_truncated = (
                dict_str[:100]
                + "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
            )
            assert evidence_list[0]["data"] == expected_truncated
        else:
            assert evidence_list[0]["data"] == dict_str

        # Integer data should be converted to string and remain unchanged (short)
        assert evidence_list[1]["data"] == "12345"

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        50,
    )
    def test_multiple_evidences_with_mixed_lengths(self):
        """Test truncation with multiple evidences of varying lengths."""
        evidence_list = [
            {"data": "a" * 25, "id": "short"},  # Within limit
            {"data": "b" * 75, "id": "long"},  # Over limit
            {"data": "c" * 50, "id": "exact"},  # Exactly at limit
            {"data": "d" * 100, "id": "very_long"},  # Way over limit
        ]

        truncate_evidences_entities_if_necessary(evidence_list)

        # Short data should remain unchanged
        assert evidence_list[0]["data"] == "a" * 25

        # Long data should be truncated
        expected_long = (
            "b" * 50 + "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
        )
        assert evidence_list[1]["data"] == expected_long

        # Exact limit should remain unchanged
        assert evidence_list[2]["data"] == "c" * 50

        # Very long data should be truncated
        expected_very_long = (
            "d" * 50 + "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
        )
        assert evidence_list[3]["data"] == expected_very_long

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        100,
    )
    def test_function_modifies_original_list(self):
        """Test that the function modifies the original list in-place."""
        long_data = "x" * 150
        evidence_list = [{"data": long_data, "id": "test-1"}]
        original_list_id = id(evidence_list)
        original_dict_id = id(evidence_list[0])

        truncate_evidences_entities_if_necessary(evidence_list)

        # The list and dictionary objects should be the same (modified in-place)
        assert id(evidence_list) == original_list_id
        assert id(evidence_list[0]) == original_dict_id

        # But the data should be different
        assert evidence_list[0]["data"] != long_data
        assert evidence_list[0]["data"].endswith(
            "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
        )

    @patch(
        "holmes.core.truncation.dal_truncation_utils.MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION",
        20,
    )
    def test_truncation_message_consistency(self):
        """Test that the truncation message is consistent."""
        long_data = "a" * 100
        evidence_list = [{"data": long_data, "id": "test-1"}]

        truncate_evidences_entities_if_necessary(evidence_list)

        expected_suffix = "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
        assert evidence_list[0]["data"].endswith(expected_suffix)
        assert evidence_list[0]["data"].startswith("a" * 20)
