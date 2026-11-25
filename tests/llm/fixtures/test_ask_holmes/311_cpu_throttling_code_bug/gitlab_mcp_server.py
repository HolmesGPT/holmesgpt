"""Read-only GitLab MCP server for the logistics group.

Mimics the GitLab MCP server's repository tools (list_projects,
get_repository_tree, get_file_contents, list_commits). The
logistics/quote-service repository is served from the files mounted at
/repo — the same Secret the running quote-service pod executes — so the
code Holmes reads over MCP is exactly the code that is running.

This is a reference copy; the deployed code is loaded from the
gitlab-mcp-code Secret created in before_test.
"""

import os
from typing import Any, Optional

from mcp.server.fastmcp.server import FastMCP

REPO_ROOT = os.getenv("REPO_ROOT", "/repo")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

PROJECTS = [
    {
        "id": 41,
        "name": "label-service",
        "path_with_namespace": "logistics/label-service",
        "description": "Renders shipping label PDFs and dispatches them to carrier portals",
        "default_branch": "main",
        "web_url": "https://gitlab.internal.example.com/logistics/label-service",
        "last_activity_at": "2026-07-02T09:14:00Z",
    },
    {
        "id": 42,
        "name": "quote-service",
        "path_with_namespace": "logistics/quote-service",
        "description": "Shipping quote API — computes carrier tariff matrices per route",
        "default_branch": "main",
        "web_url": "https://gitlab.internal.example.com/logistics/quote-service",
        "last_activity_at": "2026-07-17T15:42:00Z",
    },
]

LABEL_SERVICE_FILES = {
    "README.md": (
        "# label-service\n\n"
        "Renders shipping label PDFs for confirmed shipments and uploads them\n"
        "to the carrier portals.\n\n"
        "Consumes `shipment.confirmed` events from the message bus; no HTTP API.\n"
    ),
    "label_worker.py": (
        '"""Label rendering worker.\n\n'
        "Consumes shipment.confirmed events and renders a PDF label per parcel.\n"
        '"""\n\n'
        "import json\n"
        "import logging\n"
        "import time\n\n"
        'logger = logging.getLogger("label_worker")\n\n\n'
        "def render_label(shipment: dict) -> bytes:\n"
        '    """Render a 10x15cm label PDF for the shipment (stub renderer)."""\n'
        '    payload = json.dumps(shipment, sort_keys=True).encode()\n'
        '    return b"%PDF-1.4\\n" + payload\n\n\n'
        "def main() -> None:\n"
        "    while True:\n"
        "        # Poll the bus for confirmed shipments (long-poll, 30s).\n"
        "        time.sleep(30)\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    ),
}

COMMITS = {
    42: [
        {
            "id": "b7f2c91a4de08f3b2a91c6e5d47a80f19c2b3d64",
            "short_id": "b7f2c91a",
            "title": "Extract cache key construction into a helper",
            "message": "Extract cache key construction into a helper\n\nNo functional change intended.",
            "author_name": "Dana Vos",
            "created_at": "2026-07-17T15:42:00Z",
        },
        {
            "id": "a41d0e77c9b28e615f3da0742cb1985e6a7d0c21",
            "short_id": "a41d0e77",
            "title": "Cache tariff matrices per route to cut CPU usage",
            "message": "Cache tariff matrices per route to cut CPU usage\n\nRate cards refresh at most daily, so compute each route once.",
            "author_name": "Priya Ramanathan",
            "created_at": "2026-06-30T11:05:00Z",
        },
        {
            "id": "90c3ffd21b6a4e89d052c7f3ea1b04d86523ac70",
            "short_id": "90c3ffd2",
            "title": "Initial import of quote service",
            "message": "Initial import of quote service",
            "author_name": "Priya Ramanathan",
            "created_at": "2026-05-12T08:30:00Z",
        },
    ],
    41: [
        {
            "id": "5e8a01bd39f7c624ab05de816c94f2073ba1e9c8",
            "short_id": "5e8a01bd",
            "title": "Initial import of label service",
            "message": "Initial import of label service",
            "author_name": "Dana Vos",
            "created_at": "2026-04-20T10:00:00Z",
        },
    ],
}


def _quote_service_files() -> dict[str, str]:
    """Read the quote-service repo from disk (mounted Secret)."""
    files = {}
    for name in sorted(os.listdir(REPO_ROOT)):
        if name.startswith("."):
            continue
        path = os.path.join(REPO_ROOT, name)
        if os.path.isfile(path):
            with open(path) as f:
                files[name] = f.read()
    return files


def _project_files(project_id: int) -> Optional[dict[str, str]]:
    if project_id == 42:
        return _quote_service_files()
    if project_id == 41:
        return LABEL_SERVICE_FILES
    return None


def _validate_ref(project_id: int, ref: str) -> Optional[dict[str, Any]]:
    """Only the default branch (and its HEAD commit) is mirrored.

    Returning main's content labelled with an arbitrary caller-provided ref
    would misrepresent history, so any other ref gets an explicit error.
    """
    commits = COMMITS.get(project_id, [])
    allowed = {"main"}
    if commits:
        allowed.add(commits[0]["id"])
        allowed.add(commits[0]["short_id"])
    if ref not in allowed:
        return {
            "error": (
                f"Ref '{ref}' is not available: this read-only gateway only "
                "mirrors the default branch 'main' at its current HEAD"
            ),
            "available_refs": sorted(allowed),
        }
    return None


def create_server() -> FastMCP:
    app = FastMCP(
        name="GitLab MCP Server",
        instructions=(
            "Read-only access to the company's GitLab instance: list projects, "
            "browse repository trees, read source files and commit history."
        ),
        host=MCP_HOST,
        port=MCP_PORT,
        streamable_http_path="/",
    )

    @app.tool()
    async def list_projects() -> dict[str, Any]:
        """List GitLab projects in the logistics group with descriptions and default branches."""
        return {"projects": PROJECTS, "total": len(PROJECTS)}

    @app.tool()
    async def get_repository_tree(project_id: int, ref: str = "main") -> dict[str, Any]:
        """List the files in a project's repository.

        Args:
            project_id: Numeric GitLab project id (see list_projects)
            ref: Branch or tag name (default: main)
        """
        files = _project_files(project_id)
        if files is None:
            return {
                "error": f"Project {project_id} not found",
                "available_projects": [project["id"] for project in PROJECTS],
            }
        ref_error = _validate_ref(project_id, ref)
        if ref_error is not None:
            return ref_error
        return {
            "project_id": project_id,
            "ref": ref,
            "tree": [{"path": path, "type": "blob"} for path in files],
        }

    @app.tool()
    async def get_file_contents(
        project_id: int, file_path: str, ref: str = "main"
    ) -> dict[str, Any]:
        """Get the raw contents of a file from a project's repository.

        Args:
            project_id: Numeric GitLab project id (see list_projects)
            file_path: Path of the file within the repository
            ref: Branch or tag name (default: main)
        """
        files = _project_files(project_id)
        if files is None:
            return {
                "error": f"Project {project_id} not found",
                "available_projects": [project["id"] for project in PROJECTS],
            }
        ref_error = _validate_ref(project_id, ref)
        if ref_error is not None:
            return ref_error
        if file_path not in files:
            return {
                "error": f"File '{file_path}' not found in project {project_id} at ref '{ref}'",
                "available_files": list(files),
            }
        return {
            "project_id": project_id,
            "file_path": file_path,
            "ref": ref,
            "content": files[file_path],
        }

    @app.tool()
    async def list_commits(project_id: int, ref: str = "main") -> dict[str, Any]:
        """List recent commits on a project's branch, newest first.

        Args:
            project_id: Numeric GitLab project id (see list_projects)
            ref: Branch or tag name (default: main)
        """
        commits = COMMITS.get(project_id)
        if commits is None:
            return {
                "error": f"Project {project_id} not found",
                "available_projects": [project["id"] for project in PROJECTS],
            }
        ref_error = _validate_ref(project_id, ref)
        if ref_error is not None:
            return ref_error
        return {"project_id": project_id, "ref": ref, "commits": commits}

    return app


def main() -> int:
    server = create_server()
    server.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
