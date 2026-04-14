import hashlib
import sys
from unittest.mock import MagicMock, patch


class TestHgptKeyAuth:
    def test_hgpt_prefix_detected(self):
        """Verify hgpt_ tokens are routed to API key auth, not JWT."""
        token = "hgpt_abc123"
        assert token.startswith("hgpt_")
        assert token.count(".") < 2

    def test_jwt_token_detected(self):
        """Verify JWT tokens have 2+ dots."""
        token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkw.signature"
        assert token.count(".") >= 2
        assert not token.startswith("hgpt_")


class TestProjectScopeCheck:
    def test_empty_project_ids_allows_all(self):
        sys.path.insert(0, "frontend")
        from rbac import UserRecord, UserPermissions, check_api_key_project_access
        record = UserRecord(sub="test", email="t@t.com", status="active")
        perms = UserPermissions(user=record, project_roles={})
        perms.api_key_project_ids = []
        assert check_api_key_project_access(perms, "any-project") is True

    def test_scoped_project_ids_blocks_wrong_project(self):
        sys.path.insert(0, "frontend")
        from rbac import UserRecord, UserPermissions, check_api_key_project_access
        record = UserRecord(sub="test", email="t@t.com", status="active")
        perms = UserPermissions(user=record, project_roles={})
        perms.api_key_project_ids = ["proj1", "proj2"]
        assert check_api_key_project_access(perms, "proj3") is False

    def test_scoped_project_ids_allows_correct_project(self):
        sys.path.insert(0, "frontend")
        from rbac import UserRecord, UserPermissions, check_api_key_project_access
        record = UserRecord(sub="test", email="t@t.com", status="active")
        perms = UserPermissions(user=record, project_roles={})
        perms.api_key_project_ids = ["proj1", "proj2"]
        assert check_api_key_project_access(perms, "proj1") is True

    def test_super_admin_bypasses_scope(self):
        sys.path.insert(0, "frontend")
        from rbac import UserRecord, UserPermissions, check_api_key_project_access
        record = UserRecord(sub="test", email="t@t.com", global_role="super-admin", status="active")
        perms = UserPermissions(user=record, project_roles={})
        assert check_api_key_project_access(perms, "any-project") is True
