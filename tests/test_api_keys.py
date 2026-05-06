import hashlib
import pytest
from unittest.mock import MagicMock, patch


def _make_store():
    """Create an ApiKeyStore with a mocked DynamoDB table."""
    with patch("api_keys._get_table") as mock_table_fn:
        mock_table = MagicMock()
        mock_table_fn.return_value = mock_table
        from api_keys import ApiKeyStore
        store = ApiKeyStore()
        return store, mock_table


class TestApiKeyGeneration:
    def test_generate_key_has_hgpt_prefix(self):
        from api_keys import generate_api_key
        key = generate_api_key()
        assert key.startswith("hgpt_")

    def test_generate_key_is_69_chars(self):
        """hgpt_ (5) + 64 hex chars = 69"""
        from api_keys import generate_api_key
        key = generate_api_key()
        assert len(key) == 69

    def test_hash_key_is_sha256(self):
        from api_keys import hash_api_key
        key = "hgpt_abc123"
        result = hash_api_key(key)
        assert result == hashlib.sha256(key.encode()).hexdigest()


class TestApiKeyStoreCreate:
    def test_create_returns_full_key(self):
        store, mock_table = _make_store()
        mock_table.put_item.return_value = {}
        result = store.create(name="test-agent", project_ids=["proj1"], created_by="admin@test.com")
        assert result["key"].startswith("hgpt_")
        assert result["name"] == "test-agent"
        assert result["project_ids"] == ["proj1"]
        mock_table.put_item.assert_called_once()

    def test_create_stores_hash_not_plaintext(self):
        store, mock_table = _make_store()
        mock_table.put_item.return_value = {}
        result = store.create(name="test", project_ids=[], created_by="admin@test.com")
        call_args = mock_table.put_item.call_args
        item = call_args[1]["Item"] if "Item" in call_args[1] else call_args[0][0]
        stored_data = str(item)
        assert result["key"] not in stored_data


class TestApiKeyStoreLookup:
    def test_lookup_active_key(self):
        from api_keys import hash_api_key
        store, mock_table = _make_store()
        key = "hgpt_" + "a" * 64
        key_hash = hash_api_key(key)
        mock_table.get_item.return_value = {
            "Item": {
                "pk": f"APIKEY#{key_hash}",
                "sk": f"APIKEY#{key_hash}",
                "data": '{"key_hash":"' + key_hash + '","key_prefix":"hgpt_aaa...","name":"test","project_ids":["p1"],"created_by":"a@b.com","created_at":"2026-01-01T00:00:00Z","last_used_at":"","status":"active"}',
            }
        }
        result = store.lookup(key)
        assert result is not None
        assert result.name == "test"
        assert result.status == "active"

    def test_lookup_revoked_key_returns_none(self):
        from api_keys import hash_api_key
        store, mock_table = _make_store()
        key = "hgpt_" + "b" * 64
        key_hash = hash_api_key(key)
        mock_table.get_item.return_value = {
            "Item": {
                "pk": f"APIKEY#{key_hash}",
                "sk": f"APIKEY#{key_hash}",
                "data": '{"key_hash":"' + key_hash + '","key_prefix":"hgpt_bbb...","name":"test","project_ids":[],"created_by":"a@b.com","created_at":"2026-01-01T00:00:00Z","last_used_at":"","status":"revoked"}',
            }
        }
        result = store.lookup(key)
        assert result is None

    def test_lookup_missing_key_returns_none(self):
        store, mock_table = _make_store()
        mock_table.get_item.return_value = {}
        result = store.lookup("hgpt_nonexistent")
        assert result is None


class TestApiKeyStoreList:
    def test_list_returns_all_active_keys(self):
        store, mock_table = _make_store()
        mock_table.scan.return_value = {
            "Items": [
                {
                    "pk": "APIKEY#hash1",
                    "sk": "APIKEY#hash1",
                    "data": '{"key_hash":"hash1","key_prefix":"hgpt_aaa...","name":"agent-1","project_ids":["p1"],"created_by":"a@b.com","created_at":"2026-01-01T00:00:00Z","last_used_at":"","status":"active"}',
                }
            ]
        }
        result = store.list()
        assert len(result) == 1
        assert result[0].name == "agent-1"


class TestApiKeyStoreRevoke:
    def test_revoke_sets_status(self):
        store, mock_table = _make_store()
        mock_table.update_item.return_value = {}
        mock_table.scan.return_value = {
            "Items": [
                {
                    "pk": "APIKEY#hash1",
                    "sk": "APIKEY#hash1",
                    "data": '{"key_hash":"hash1","key_prefix":"hgpt_aaa...","name":"test","project_ids":[],"created_by":"a@b.com","created_at":"2026-01-01T00:00:00Z","last_used_at":"","status":"active"}',
                }
            ]
        }
        store.revoke("hgpt_aaa...")
        mock_table.put_item.assert_called_once()
