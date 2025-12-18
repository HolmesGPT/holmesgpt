ALLOWED_HELM_COMMANDS: dict[str, dict] = {
    # Release management (read-only)
    "list": {},
    "ls": {},
    "get": {
        "all": {},
        "hooks": {},
        "manifest": {},
        "notes": {},
        "values": {},
        "metadata": {},
    },
    "status": {},
    "history": {},
    "diff": {
        "upgrade": {},
        "rollback": {},
        "revision": {},
        "release": {},
        "version": {},
    },
    # Chart operations (read-only)
    "show": {
        "all": {},
        "chart": {},
        "readme": {},
        "values": {},
        "crds": {},
    },
    "inspect": {},
    "search": {
        "repo": {},
    },
    "lint": {},
    "verify": {},
    "dependency": {"list": {}},
    # Repository operations (read-only)
    "repo": {"list": {}},
    # Help and information
    "help": {},
    "version": {},
    # Plugin operations (read-only)
    "plugin": {
        "list": {},
    },
    # Completion
    "completion": {},
}

# Blocked Helm operations (state-modifying or dangerous)
DENIED_HELM_COMMANDS: dict[str, dict] = {
    # Release lifecycle operations
    "install": {},
    "upgrade": {},
    "uninstall": {},
    "delete": {},
    "rollback": {},
    "test": {},
    # Repository management
    "repo": {
        "add": {},
        "remove": {},
        "rm": {},
        "update": {},
        "index": {},
    },
    # Chart packaging and publishing
    "create": {},
    "package": {},
    "push": {},
    "pull": {},
    "fetch": {},
    "dependency": {
        "update": {},
        "build": {},
    },
    # Plugin management
    "plugin": {
        "install": {},
        "uninstall": {},
        "update": {},
    },
    # Registry operations
    "registry": {},
    # Environment modification
    "env": {},
    # Configuration modification
    "config": {},
    # Mapkubeapis operations
    "mapkubeapis": {},
    "template": {},
}
