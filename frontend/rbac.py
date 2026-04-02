"""
RBAC (Role-Based Access Control) module for HolmesGPT.

Manages user records and project-level role assignments in DynamoDB.
Uses the same single-table design as projects.py.

New entity types:
  USER#<okta_sub>       | META            -> UserRecord JSON
  USER#email:<email>    | META            -> UserRecord JSON (invited, pre-login)
  USER#<okta_sub>       | PROJECT#<id>    -> ProjectRole JSON
  USER#email:<email>    | PROJECT#<id>    -> ProjectRole JSON (invited)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key
from pydantic import BaseModel

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
SUPER_ADMIN_EMAIL = os.environ.get("HOLMES_SUPER_ADMIN_EMAIL", "")

# Permission cache: sub -> (timestamp, UserPermissions)
_CACHE_TTL = 300  # 5 minutes
_permission_cache: dict[str, tuple[float, "UserPermissions"]] = {}


# ── Data models ──────────────────────────────────────────────────────────────


class UserRecord(BaseModel):
    sub: str  # Okta sub or "email:<email>" for invited users
    email: str
    name: Optional[str] = None
    global_role: Optional[str] = None  # "super-admin" or None
    status: str = "active"  # "active", "invited"
    created_at: str = ""
    last_login: Optional[str] = None


class ProjectRole(BaseModel):
    project_id: str
    role: str  # "project-admin" or "read-only"
    assigned_by: str = ""
    assigned_at: str = ""


class UserPermissions(BaseModel):
    user: UserRecord
    project_roles: dict[str, ProjectRole] = {}  # project_id -> ProjectRole


# ── DynamoDB helpers ─────────────────────────────────────────────────────────


def _get_table():
    return boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)


def _user_pk(sub: str) -> str:
    """Build the partition key for a user."""
    return f"USER#{sub}"


def _email_pk(email: str) -> str:
    """Build the partition key for an invited (email-keyed) user."""
    return f"USER#email:{email.lower()}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Cache ────────────────────────────────────────────────────────────────────


def invalidate_cache(sub: str) -> None:
    """Remove a user's cached permissions."""
    _permission_cache.pop(sub, None)


def _get_cached(sub: str) -> UserPermissions | None:
    """Return cached permissions if still valid."""
    entry = _permission_cache.get(sub)
    if entry is None:
        return None
    cached_at, perms = entry
    if time.time() - cached_at > _CACHE_TTL:
        _permission_cache.pop(sub, None)
        return None
    return perms


def _set_cached(sub: str, perms: UserPermissions) -> None:
    """Cache a user's permissions."""
    _permission_cache[sub] = (time.time(), perms)


# ── User CRUD ────────────────────────────────────────────────────────────────


def get_user(sub: str) -> UserRecord | None:
    """Get a user by their Okta sub."""
    resp = _get_table().get_item(Key={"pk": _user_pk(sub), "sk": "META"})
    item = resp.get("Item")
    if not item:
        return None
    return UserRecord.model_validate_json(item["data"])


def get_user_by_email(email: str) -> UserRecord | None:
    """Get an invited user by their email address."""
    resp = _get_table().get_item(Key={"pk": _email_pk(email), "sk": "META"})
    item = resp.get("Item")
    if not item:
        return None
    return UserRecord.model_validate_json(item["data"])


def create_user(
    sub: str,
    email: str,
    name: str,
    global_role: str | None = None,
) -> UserRecord:
    """Create a new active user record."""
    user = UserRecord(
        sub=sub,
        email=email.lower(),
        name=name,
        global_role=global_role,
        status="active",
        created_at=_now_iso(),
        last_login=_now_iso(),
    )
    _get_table().put_item(
        Item={"pk": _user_pk(sub), "sk": "META", "data": user.model_dump_json()}
    )
    return user


def create_invited_user(email: str) -> UserRecord:
    """Create a placeholder user record for an invited email."""
    user = UserRecord(
        sub=f"email:{email.lower()}",
        email=email.lower(),
        name=None,
        global_role=None,
        status="invited",
        created_at=_now_iso(),
    )
    _get_table().put_item(
        Item={"pk": _email_pk(email), "sk": "META", "data": user.model_dump_json()}
    )
    return user


def update_user_login(sub: str) -> None:
    """Update the last_login timestamp for a user."""
    user = get_user(sub)
    if user:
        user.last_login = _now_iso()
        _get_table().put_item(
            Item={"pk": _user_pk(sub), "sk": "META", "data": user.model_dump_json()}
        )


def link_invited_user(sub: str, email: str, name: str) -> UserRecord | None:
    """
    Migrate an email-keyed invited user to a sub-keyed active user.

    1. Read the invited user record and all their project role assignments
    2. Create new sub-keyed records with the same data
    3. Delete the old email-keyed records
    """
    table = _get_table()
    email_pk = _email_pk(email)

    # Read all items for this invited user (META + PROJECT#*)
    resp = table.query(KeyConditionExpression=Key("pk").eq(email_pk))
    items = resp.get("Items", [])
    if not items:
        return None

    # Find the META record
    invited_user = None
    project_items = []
    for item in items:
        if item["sk"] == "META":
            invited_user = UserRecord.model_validate_json(item["data"])
        else:
            project_items.append(item)

    if not invited_user:
        return None

    # Create the new active user
    active_user = UserRecord(
        sub=sub,
        email=email.lower(),
        name=name,
        global_role=invited_user.global_role,
        status="active",
        created_at=invited_user.created_at,
        last_login=_now_iso(),
    )
    new_pk = _user_pk(sub)

    # Write new sub-keyed records
    table.put_item(
        Item={"pk": new_pk, "sk": "META", "data": active_user.model_dump_json()}
    )
    for item in project_items:
        table.put_item(
            Item={"pk": new_pk, "sk": item["sk"], "data": item["data"]}
        )

    # Delete old email-keyed records
    for item in items:
        table.delete_item(Key={"pk": email_pk, "sk": item["sk"]})

    return active_user


# ── Permissions ──────────────────────────────────────────────────────────────


def get_user_permissions(sub: str) -> UserPermissions | None:
    """
    Load a user's full permissions (profile + all project roles).
    Results are cached for 5 minutes.
    """
    # Check cache
    cached = _get_cached(sub)
    if cached is not None:
        return cached

    table = _get_table()
    pk = _user_pk(sub)

    # Query all items for this user (META + PROJECT#*)
    resp = table.query(KeyConditionExpression=Key("pk").eq(pk))
    items = resp.get("Items", [])
    if not items:
        return None

    user_record = None
    project_roles: dict[str, ProjectRole] = {}

    for item in items:
        if item["sk"] == "META":
            user_record = UserRecord.model_validate_json(item["data"])
        elif item["sk"].startswith("PROJECT#"):
            project_id = item["sk"].replace("PROJECT#", "")
            role = ProjectRole.model_validate_json(item["data"])
            project_roles[project_id] = role

    if not user_record:
        return None

    perms = UserPermissions(user=user_record, project_roles=project_roles)
    _set_cached(sub, perms)
    return perms


# ── Role assignment ──────────────────────────────────────────────────────────


def set_global_role(sub: str, role: str | None, assigned_by: str) -> None:
    """Set or remove a user's global role (super-admin)."""
    # Try sub-keyed user first, then email-keyed
    user = get_user(sub)
    pk = _user_pk(sub)
    if not user:
        # Check if sub is actually an email-keyed identifier
        if sub.startswith("email:"):
            email = sub.replace("email:", "")
            user = get_user_by_email(email)
            pk = _email_pk(email)
    if not user:
        raise ValueError(f"User not found: {sub}")

    user.global_role = role
    _get_table().put_item(
        Item={"pk": pk, "sk": "META", "data": user.model_dump_json()}
    )
    invalidate_cache(sub)


def set_project_role(
    sub: str,
    project_id: str,
    role: str | None,
    assigned_by: str,
) -> None:
    """
    Set or remove a user's role on a project.
    role=None removes the assignment.
    """
    # Determine the correct pk
    pk = _user_pk(sub)
    user = get_user(sub)
    if not user and sub.startswith("email:"):
        email = sub.replace("email:", "")
        user = get_user_by_email(email)
        pk = _email_pk(email)
    if not user:
        raise ValueError(f"User not found: {sub}")

    table = _get_table()
    sk = f"PROJECT#{project_id}"

    if role is None:
        # Remove the assignment
        table.delete_item(Key={"pk": pk, "sk": sk})
    else:
        pr = ProjectRole(
            project_id=project_id,
            role=role,
            assigned_by=assigned_by,
            assigned_at=_now_iso(),
        )
        table.put_item(Item={"pk": pk, "sk": sk, "data": pr.model_dump_json()})

    invalidate_cache(sub)


# ── Listing ──────────────────────────────────────────────────────────────────


def list_users() -> list[UserRecord]:
    """List all users (both active and invited)."""
    table = _get_table()
    filter_expr = Attr("pk").begins_with("USER#") & Attr("sk").eq("META")
    items: list = []
    kwargs: dict = {"FilterExpression": filter_expr}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    users = [UserRecord.model_validate_json(item["data"]) for item in items]
    return sorted(users, key=lambda u: u.created_at)


def get_project_users(project_id: str) -> list[tuple[UserRecord, str]]:
    """
    Get all users assigned to a project with their roles.
    Uses the GSI (gsi-sk-pk) for reverse lookup.
    Returns list of (UserRecord, role_string) tuples.
    """
    table = _get_table()
    sk = f"PROJECT#{project_id}"

    resp = table.query(
        IndexName="gsi-sk-pk",
        KeyConditionExpression=Key("sk").eq(sk),
    )

    results = []
    for item in resp.get("Items", []):
        pk = item["pk"]
        sub = pk.replace("USER#", "")
        role_data = ProjectRole.model_validate_json(item["data"])

        # Look up the user record
        user = get_user(sub)
        if not user and sub.startswith("email:"):
            email = sub.replace("email:", "")
            user = get_user_by_email(email)
        if user:
            results.append((user, role_data.role))

    return results


def delete_project_roles(project_id: str) -> None:
    """
    Delete all user-project role assignments for a project.
    Called when a project is deleted.
    """
    table = _get_table()
    sk = f"PROJECT#{project_id}"

    # Use GSI to find all assignments for this project
    resp = table.query(
        IndexName="gsi-sk-pk",
        KeyConditionExpression=Key("sk").eq(sk),
    )

    for item in resp.get("Items", []):
        table.delete_item(Key={"pk": item["pk"], "sk": sk})
        # Invalidate cache for the affected user
        sub = item["pk"].replace("USER#", "")
        invalidate_cache(sub)


def delete_user(sub: str) -> bool:
    """Delete a user and all their role assignments."""
    table = _get_table()

    # Determine the correct pk
    pk = _user_pk(sub)
    user = get_user(sub)
    if not user and sub.startswith("email:"):
        email = sub.replace("email:", "")
        user = get_user_by_email(email)
        pk = _email_pk(email)
    if not user:
        return False

    # Query all items for this user
    resp = table.query(KeyConditionExpression=Key("pk").eq(pk))
    items = resp.get("Items", [])

    # Delete all items
    for item in items:
        table.delete_item(Key={"pk": pk, "sk": item["sk"]})

    invalidate_cache(sub)
    return True


# ── Bootstrap ────────────────────────────────────────────────────────────────


def ensure_user_exists(sub: str, email: str, name: str) -> UserPermissions:
    """
    Called on every authenticated request. Ensures user exists in DynamoDB.

    Flow:
    1. Check USER#<sub> exists -> update last_login, return permissions
    2. Check USER#email:<email> exists -> link_invited_user, return permissions
    3. Create new user with status="active", role=None
    4. If email matches HOLMES_SUPER_ADMIN_EMAIL -> set role="super-admin"
    5. Return permissions
    """
    # 1. Check if user already exists by sub
    perms = get_user_permissions(sub)
    if perms is not None:
        # Update last_login (async-safe, non-blocking for the request)
        update_user_login(sub)
        return perms

    # 2. Check if there's an invited user with this email
    invited = get_user_by_email(email)
    if invited:
        linked_user = link_invited_user(sub, email, name)
        if linked_user:
            perms = get_user_permissions(sub)
            if perms:
                return perms

    # 3. Create new user
    global_role = None
    if SUPER_ADMIN_EMAIL and email.lower() == SUPER_ADMIN_EMAIL.lower():
        global_role = "super-admin"
        logger.info("Bootstrap: granting super-admin to %s", email)

    user = create_user(sub, email, name, global_role=global_role)

    # Build permissions (no project roles yet for new users)
    perms = UserPermissions(user=user, project_roles={})
    _set_cached(sub, perms)
    return perms
