ALLOWED_ARGOCD_COMMANDS: dict[str, dict] = {
    # Application management (read-only)
    "app": {
        "list": {},
        "get": {},
        "resources": {},
        "diff": {},
        "history": {},
        "manifests": {},
        "logs": {},
        "wait": {},  # Wait for app to reach desired state (read-only monitoring)
    },
    # Cluster management (read-only)
    "cluster": {
        "list": {},
        "get": {},
    },
    # Project management (read-only)
    "proj": {
        "list": {},
        "get": {},
    },
    # Repository management (read-only)
    "repo": {
        "list": {},
        "get": {},
    },
    # Context management (read-only operations)
    "context": {},
    # Version information (completely safe)
    "version": {},
    # Account information (read-only)
    "account": {
        "list": {},
        "get": {},
        "get-user-info": {},
        "can-i": {},
    },
    # Administrative commands (limited read-only)
    "admin": {
        "dashboard": {},  # Starts read-only web UI
        "settings": {},  # Shows/validates settings (read-only)
    },
}

DENIED_ARGOCD_COMMANDS: dict[str, dict] = {
    # Authentication operations (sensitive)
    "login": {},
    "logout": {},
    "relogin": {},
    # Application lifecycle operations (state-modifying)
    "app": {
        "create": {},
        "delete": {},
        "sync": {},
        "rollback": {},
        "edit": {},
        "set": {},
        "unset": {},
        "patch": {},
        "delete-resource": {},
        "terminate-op": {},
        "actions": {},  # Custom resource actions
    },
    # Account management (sensitive)
    "account": {
        "generate-token": {},
        "delete-token": {},
        "update-password": {},
        "bcrypt": {},
    },
    # Cluster management (state-modifying)
    "cluster": {
        "add": {},
        "rm": {},
        "set": {},
        "rotate-auth": {},
    },
    # Project management (state-modifying)
    "proj": {
        "create": {},
        "delete": {},
        "edit": {},
        "add-source": {},
        "remove-source": {},
        "add-destination": {},
        "remove-destination": {},
        "add-cluster-resource-whitelist": {},
        "remove-cluster-resource-whitelist": {},
        "add-namespace-resource-blacklist": {},
        "remove-namespace-resource-blacklist": {},
        "add-cluster-resource-blacklist": {},
        "remove-cluster-resource-blacklist": {},
        "add-namespace-resource-whitelist": {},
        "remove-namespace-resource-whitelist": {},
        "add-orphaned-ignore": {},
        "remove-orphaned-ignore": {},
        "add-signature-key": {},
        "remove-signature-key": {},
        "set-orphaned-ignore": {},
        "add-role": {},
        "remove-role": {},
        "add-role-token": {},
        "delete-role-token": {},
        "set-role": {},
    },
    # Repository management (state-modifying)
    "repo": {
        "add": {},
        "rm": {},
    },
    # Certificate management
    "cert": {},
    # GPG key management
    "gpg": {},
    # Application set operations
    "appset": {},
    # Notification operations
    "notifications": {},
}
