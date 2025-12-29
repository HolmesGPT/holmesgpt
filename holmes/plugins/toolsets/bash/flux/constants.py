ALLOWED_FLUX_COMMANDS: dict[str, dict] = {
    # Get commands (read-only status queries)
    "get": {
        "all": {},
        "sources": {},
        "kustomizations": {},
        "helmreleases": {},
        "alerts": {},
        "alert-providers": {},
        "receivers": {},
        "images": {},
        "artifacts": {},
    },
    # Check command (health verification)
    "check": {},
    # Version information (completely safe)
    "version": {},
    # Stats (read-only metrics)
    "stats": {},
    # Logs (read-only log viewing)
    "logs": {},
    # Events (read-only event viewing)
    "events": {},
    # Trace (read-only dependency tracing)
    "trace": {},
    # Tree (read-only resource tree)
    "tree": {},
    # Debug commands (read-only inspection)
    "debug": {
        "kustomization": {},
        "helmrelease": {},
    },
    # Diff (read-only comparison)
    "diff": {
        "kustomization": {},
        "artifact": {},
    },
    # Export (read-only YAML export)
    "export": {
        "source": {},
        "kustomization": {},
        "helmrelease": {},
        "alert": {},
        "alert-provider": {},
        "receiver": {},
        "image": {},
    },
}

DENIED_FLUX_COMMANDS: dict[str, dict] = {
    # Reconcile operations (state-modifying - triggers sync)
    "reconcile": {},
    # Suspend operations (state-modifying - pauses reconciliation)
    "suspend": {},
    # Resume operations (state-modifying - resumes reconciliation)
    "resume": {},
    # Create operations (state-modifying)
    "create": {},
    # Delete operations (state-modifying)
    "delete": {},
    # Bootstrap operations (cluster modification)
    "bootstrap": {},
    # Install/Uninstall operations
    "install": {},
    "uninstall": {},
    # Push operations (artifact modification)
    "push": {},
    # Pull operations (can trigger reconciliation)
    "pull": {},
    # Tag operations (artifact modification)
    "tag": {},
    # Build operations
    "build": {},
    # Completion (shell modification)
    "completion": {},
}
