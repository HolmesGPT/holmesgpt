"""
User management API endpoints for HolmesGPT (super-admin only).

Provides CRUD operations for users and their role assignments.
All endpoints require super-admin permissions.
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import rbac

logger = logging.getLogger(__name__)


def _require_super_admin(request: Request) -> None:
    """Raise 403 if the current user is not a super-admin."""
    perms: rbac.UserPermissions = request.state.permissions
    if perms.user.global_role != "super-admin":
        raise HTTPException(status_code=403, detail="Super-admin required")


def _serialize_user_with_roles(user: rbac.UserRecord) -> dict:
    """Serialize a user record with their project roles."""
    perms = rbac.get_user_permissions(user.sub)
    project_roles = {}
    if perms:
        for pid, pr in perms.project_roles.items():
            project_roles[pid] = {
                "project_id": pr.project_id,
                "role": pr.role,
                "assigned_by": pr.assigned_by,
                "assigned_at": pr.assigned_at,
            }

    return {
        "sub": user.sub,
        "email": user.email,
        "name": user.name,
        "global_role": user.global_role,
        "status": user.status,
        "created_at": user.created_at,
        "last_login": user.last_login,
        "project_roles": project_roles,
    }


def mount_users_api(app: FastAPI) -> None:
    """Register user management endpoints on the FastAPI app."""

    @app.get("/api/users")
    async def list_users(request: Request):
        """List all users with their roles (super-admin only)."""
        _require_super_admin(request)
        try:
            users = rbac.list_users()
            return JSONResponse([_serialize_user_with_roles(u) for u in users])
        except Exception as e:
            logger.error("Failed to list users: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/users/invite")
    async def invite_user(request: Request):
        """Invite a user by email (super-admin only)."""
        _require_super_admin(request)
        try:
            body = await request.json()
            email = body.get("email", "").strip().lower()
            if not email:
                raise HTTPException(status_code=400, detail="Email is required")

            # Check if user already exists
            existing = rbac.get_user_by_email(email)
            if existing:
                raise HTTPException(status_code=409, detail="User already invited")

            # Also check active users by scanning (less efficient but covers the case)
            for u in rbac.list_users():
                if u.email.lower() == email:
                    raise HTTPException(status_code=409, detail="User already exists")

            user = rbac.create_invited_user(email)
            return JSONResponse(user.model_dump(), status_code=201)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to invite user: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/users/{user_id}/global-role")
    async def update_user_global_role(user_id: str, request: Request):
        """Set or remove a user's global role (super-admin only)."""
        _require_super_admin(request)
        try:
            body = await request.json()
            role = body.get("role")  # "super-admin" or None
            if role is not None and role != "super-admin":
                raise HTTPException(status_code=400, detail="Invalid role. Must be 'super-admin' or null.")

            admin_sub = request.state.permissions.user.sub
            rbac.set_global_role(user_id, role, assigned_by=admin_sub)
            rbac.invalidate_cache(user_id)

            user = rbac.get_user(user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            return JSONResponse(user.model_dump())
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error("Failed to update global role: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/users/{user_id}/projects/{project_id}/role")
    async def update_user_project_role(user_id: str, project_id: str, request: Request):
        """Set or remove a user's role on a project (super-admin only)."""
        _require_super_admin(request)
        try:
            body = await request.json()
            role = body.get("role")  # "project-admin", "read-only", or None (remove)
            if role is not None and role not in ("project-admin", "read-only"):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid role. Must be 'project-admin', 'read-only', or null.",
                )

            admin_sub = request.state.permissions.user.sub
            rbac.set_project_role(user_id, project_id, role, assigned_by=admin_sub)
            rbac.invalidate_cache(user_id)

            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error("Failed to update project role: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/users/{user_id}")
    async def delete_user_endpoint(user_id: str, request: Request):
        """Delete a user and all their role assignments (super-admin only)."""
        _require_super_admin(request)
        try:
            # Prevent self-deletion
            if user_id == request.state.permissions.user.sub:
                raise HTTPException(status_code=400, detail="Cannot delete your own account")

            if not rbac.delete_user(user_id):
                raise HTTPException(status_code=404, detail="User not found")

            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to delete user: %s", e)
            raise HTTPException(status_code=500, detail=str(e))
