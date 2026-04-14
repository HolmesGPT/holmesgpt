"""Per-client API key store backed by DynamoDB."""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr
from pydantic import BaseModel

TABLE_NAME = os.environ.get("HOLMES_DYNAMODB_TABLE", "holmesgpt-dev-config")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _get_table():
    return boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)


def generate_api_key() -> str:
    """Generate a new API key: hgpt_ + 64 hex chars."""
    return f"hgpt_{secrets.token_hex(32)}"


def hash_api_key(key: str) -> str:
    """SHA-256 hash of a raw API key."""
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKeyRecord(BaseModel):
    key_hash: str
    key_prefix: str
    name: str
    project_ids: list[str] = []
    created_by: str
    created_at: str
    last_used_at: str = ""
    status: str = "active"


class ApiKeyStore:
    def __init__(self) -> None:
        # Capture the table reference at construction time so that unit tests
        # can patch _get_table before instantiating and the mock stays active.
        self._table = _get_table()

    def create(self, name: str, project_ids: list[str], created_by: str) -> dict:
        """Create a new API key. Returns dict with 'key' (shown once) and metadata."""
        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        now = datetime.now(timezone.utc).isoformat()

        record = ApiKeyRecord(
            key_hash=key_hash,
            key_prefix=raw_key[:13] + "...",
            name=name,
            project_ids=project_ids,
            created_by=created_by,
            created_at=now,
        )

        self._table.put_item(
            Item={
                "pk": f"APIKEY#{key_hash}",
                "sk": f"APIKEY#{key_hash}",
                "data": record.model_dump_json(),
            }
        )

        return {"key": raw_key, **record.model_dump()}

    def lookup(self, raw_key: str) -> Optional[ApiKeyRecord]:
        """Look up a key by its raw value. Returns None if not found or revoked."""
        key_hash = hash_api_key(raw_key)
        resp = self._table.get_item(
            Key={"pk": f"APIKEY#{key_hash}", "sk": f"APIKEY#{key_hash}"}
        )
        item = resp.get("Item")
        if not item:
            return None
        record = ApiKeyRecord.model_validate_json(item["data"])
        if record.status != "active":
            return None
        return record

    def touch_last_used(self, key_hash: str) -> None:
        """Update last_used_at timestamp (best-effort, non-blocking)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            item = self._table.get_item(
                Key={"pk": f"APIKEY#{key_hash}", "sk": f"APIKEY#{key_hash}"}
            )
            if not item.get("Item"):
                return
            record = ApiKeyRecord.model_validate_json(item["Item"]["data"])
            record.last_used_at = now
            self._table.put_item(
                Item={
                    "pk": f"APIKEY#{key_hash}",
                    "sk": f"APIKEY#{key_hash}",
                    "data": record.model_dump_json(),
                }
            )
        except Exception:
            logging.debug("Failed to update last_used_at for key %s", key_hash[:8], exc_info=True)

    def list(self) -> list[ApiKeyRecord]:
        """List all API keys (metadata only, never the raw key)."""
        filter_expr = Attr("pk").begins_with("APIKEY#") & Attr("sk").begins_with("APIKEY#")
        items: list = []
        kwargs: dict = {"FilterExpression": filter_expr}
        while True:
            resp = self._table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return [ApiKeyRecord.model_validate_json(item["data"]) for item in items]

    def revoke(self, key_prefix: str) -> None:
        """Revoke a key by its display prefix."""
        all_keys = self.list()
        for record in all_keys:
            if record.key_prefix == key_prefix:
                record.status = "revoked"
                self._table.put_item(
                    Item={
                        "pk": f"APIKEY#{record.key_hash}",
                        "sk": f"APIKEY#{record.key_hash}",
                        "data": record.model_dump_json(),
                    }
                )
                return
        raise ValueError(f"API key with prefix '{key_prefix}' not found")
