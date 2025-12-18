ALLOWED_DOCKER_COMMANDS: dict[str, dict] = {
    # Container management (read-only)
    "ps": {},
    "container": {
        "ls": {},
        "list": {},
        "inspect": {},
        "logs": {},
        "stats": {},
        "top": {},
        "port": {},
        "diff": {},
    },
    # Image management (read-only)
    "images": {},
    "image": {
        "ls": {},
        "list": {},
        "inspect": {},
        "history": {},
    },
    # System information
    "version": {},
    "info": {},
    "system": {
        "info": {},
        "events": {},
        "df": {},
    },
    # Network inspection
    "network": {
        "ls": {},
        "list": {},
        "inspect": {},
    },
    # Volume inspection
    "volume": {
        "ls": {},
        "list": {},
        "inspect": {},
    },
    # Registry operations (read-only)
    "search": {},
    # Plugin inspection
    "plugin": {
        "ls": {},
        "list": {},
        "inspect": {},
    },
    # Node information (Swarm read-only)
    "node": {
        "ls": {},
        "list": {},
        "inspect": {},
    },
    # Service information (Swarm read-only)
    "service": {
        "ls": {},
        "list": {},
        "inspect": {},
        "logs": {},
        "ps": {},
    },
    # Stack information (read-only)
    "stack": {
        "ls": {},
        "list": {},
        "ps": {},
        "services": {},
    },
    # Secret inspection (read-only metadata)
    "secret": {
        "ls": {},
        "list": {},
        "inspect": {},
    },
    # Config inspection (read-only)
    "config": {
        "ls": {},
        "list": {},
        "inspect": {},
    },
    # Context information
    "context": {
        "ls": {},
        "list": {},
        "inspect": {},
        "show": {},
    },
}

# Blocked Docker operations (state-modifying or dangerous)
DENIED_DOCKER_COMMANDS: dict[str, dict] = {
    # Container lifecycle operations
    "run": {},
    "create": {},
    "start": {},
    "stop": {},
    "restart": {},
    "pause": {},
    "unpause": {},
    "kill": {},
    "remove": {},
    "rm": {},
    "exec": {},
    "attach": {},
    "cp": {},
    "commit": {},
    "update": {},
    "rename": {},
    "wait": {},
    "container": {
        "create": {},
        "start": {},
        "stop": {},
        "restart": {},
        "pause": {},
        "unpause": {},
        "kill": {},
        "remove": {},
        "rm": {},
        "exec": {},
        "attach": {},
        "cp": {},
        "commit": {},
        "update": {},
        "rename": {},
        "wait": {},
        "prune": {},
        "export": {},  # Can exfiltrate full filesystem
    },
    # Image operations
    "build": {},
    "pull": {},
    "push": {},
    "tag": {},
    "untag": {},
    "rmi": {},
    "load": {},
    "import": {},
    "save": {},
    "image": {
        "build": {},
        "pull": {},
        "push": {},
        "tag": {},
        "untag": {},
        "rm": {},
        "remove": {},
        "load": {},
        "import": {},
        "save": {},
        "prune": {},
    },
    # Network operations
    "network": {
        "create": {},
        "rm": {},
        "remove": {},
        "connect": {},
        "disconnect": {},
        "prune": {},
    },
    # Volume operations
    "volume": {
        "create": {},
        "rm": {},
        "remove": {},
        "prune": {},
    },
    # System operations
    "system": {
        "prune": {},
    },
    # Registry operations
    "login": {},
    "logout": {},
    # Plugin operations
    "plugin": {
        "install": {},
        "enable": {},
        "disable": {},
        "upgrade": {},
        "rm": {},
        "remove": {},
        "push": {},
        "create": {},
        "set": {},
    },
    # Swarm operations
    "swarm": {
        "init": {},
        "join": {},
        "leave": {},
        "update": {},
        "join-token": {},
        "unlock": {},
        "unlock-key": {},
    },
    # Node operations
    "node": {
        "update": {},
        "demote": {},
        "promote": {},
        "rm": {},
        "remove": {},
    },
    # Service operations
    "service": {
        "create": {},
        "update": {},
        "scale": {},
        "rm": {},
        "remove": {},
        "rollback": {},
    },
    # Stack operations
    "stack": {
        "deploy": {},
        "rm": {},
        "remove": {},
    },
    # Secret operations
    "secret": {
        "create": {},
        "rm": {},
        "remove": {},
    },
    # Config operations
    "config": {
        "create": {},
        "rm": {},
        "remove": {},
    },
    # Context operations
    "context": {
        "create": {},
        "rm": {},
        "remove": {},
        "update": {},
        "use": {},
        "export": {},
        "import": {},
    },
    # Checkpoint operations
    "checkpoint": {},
    # Buildx operations
    "buildx": {},
    # Compose operations
    "compose": {},
    # Trust operations
    "trust": {},
    # Manifest operations
    "manifest": {},
}
