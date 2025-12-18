ALLOWED_AZURE_COMMANDS: dict[str, dict] = {
    # Basic account and resource management (read-only)
    "account": {"list": {}, "show": {}, "list-locations": {}, "tenant": {"list": {}}},
    "group": {"list": {}, "show": {}, "exists": {}},
    "resource": {"list": {}, "show": {}},
    # Virtual Machine commands (read-only)
    "vm": {
        "list": {},
        "show": {},
        "list-ip-addresses": {},
        "list-sizes": {},
        "list-skus": {},
        "list-usage": {},
        "list-vm-resize-options": {},
        "get-instance-view": {},
    },
    # Network commands (read-only)
    "network": {
        "vnet": {"list": {}, "show": {}, "subnet": {"list": {}, "show": {}}},
        "nsg": {"list": {}, "show": {}, "rule": {"list": {}, "show": {}}},
        "public-ip": {"list": {}, "show": {}},
        "lb": {
            "list": {},
            "show": {},
            "frontend-ip": {"list": {}, "show": {}},
            "rule": {"list": {}, "show": {}},
        },
        "application-gateway": {"list": {}, "show": {}, "show-backend-health": {}},
        "nic": {
            "list": {},
            "show": {},
            "list-effective-nsg": {},
            "show-effective-route-table": {},
        },
        "route-table": {"list": {}, "show": {}, "route": {"list": {}, "show": {}}},
        "dns": {
            "zone": {"list": {}, "show": {}, "record-set": {"list": {}, "show": {}}}
        },
        "traffic-manager": {
            "profile": {"list": {}, "show": {}, "endpoint": {"list": {}, "show": {}}}
        },
    },
    # Storage commands (read-only)
    "storage": {
        "account": {"list": {}, "show": {}, "show-usage": {}, "check-name": {}},
        "container": {"list": {}, "show": {}, "exists": {}},
        "blob": {"list": {}, "show": {}, "exists": {}},
        "share": {"list": {}, "show": {}, "exists": {}},
        "queue": {"list": {}, "show": {}, "exists": {}},
        "table": {"list": {}, "show": {}, "exists": {}},
    },
    # Azure Kubernetes Service (read-only)
    "aks": {
        "list": {},
        "show": {},
        "get-versions": {},
        "get-upgrades": {},
        "nodepool": {"list": {}, "show": {}},
        "check-acr": {},
    },
    # Monitoring (read-only)
    "monitor": {
        "metrics": {
            "list": {},
            "list-definitions": {},
            "list-namespaces": {},
            "alert": {"list": {}, "show": {}},
        },
        "activity-log": {
            "list": {},
            "list-categories": {},
            "alert": {"list": {}, "show": {}},
        },
        "log-analytics": {
            "workspace": {
                "list": {},
                "show": {},
                "get-schema": {},
            },
            "query": {},
        },
        "diagnostic-settings": {"list": {}, "show": {}},
        "autoscale": {"list": {}, "show": {}},
    },
    # App Service (read-only)
    "appservice": {"plan": {"list": {}, "show": {}}, "list-locations": {}},
    "webapp": {"list": {}, "show": {}, "list-runtimes": {}, "config": {"show": {}}},
    # Key Vault (limited safe operations)
    "keyvault": {"list": {}, "show": {}, "list-deleted": {}, "check-name": {}},
    # SQL Database (read-only)
    "sql": {
        "server": {"list": {}, "show": {}},
        "db": {"list": {}, "show": {}, "list-editions": {}, "show-usage": {}},
        "elastic-pool": {"list": {}, "show": {}},
    },
    # CosmosDB (read-only)
    "cosmosdb": {
        "list": {},
        "show": {},
        "database": {"list": {}, "show": {}},
        "collection": {"list": {}, "show": {}},
    },
    # Container Registry (read-only)
    "acr": {
        "list": {},
        "show": {},
        "repository": {"list": {}, "show": {}, "show-tags": {}, "show-manifests": {}},
        "check-name": {},
        "check-health": {},
    },
    # Container Instances (read-only)
    "container": {"list": {}, "show": {}, "logs": {}},
    # Batch (read-only)
    "batch": {
        "account": {"list": {}, "show": {}},
        "pool": {"list": {}, "show": {}},
        "job": {"list": {}, "show": {}},
        "task": {"list": {}, "show": {}},
    },
    # CDN (read-only)
    "cdn": {"profile": {"list": {}, "show": {}}, "endpoint": {"list": {}, "show": {}}},
    # Event Hub (read-only)
    "eventhubs": {
        "namespace": {"list": {}, "show": {}},
        "eventhub": {"list": {}, "show": {}},
    },
    # Service Bus (read-only)
    "servicebus": {
        "namespace": {"list": {}, "show": {}},
        "queue": {"list": {}, "show": {}},
        "topic": {"list": {}, "show": {}},
    },
    # IoT Hub (read-only)
    "iot": {"hub": {"list": {}, "show": {}}, "device": {"list": {}, "show": {}}},
    # Logic Apps (read-only)
    "logic": {"workflow": {"list": {}, "show": {}}},
    # Functions (read-only)
    "functionapp": {"list": {}, "show": {}, "config": {"show": {}}},
    # Redis Cache (read-only)
    "redis": {"list": {}, "show": {}},
    # Search (read-only)
    "search": {"service": {"list": {}, "show": {}}},
    # API Management (read-only)
    "apim": {"list": {}, "show": {}, "api": {"list": {}, "show": {}}},
    # Help and information
    "help": {},
    "version": {},
    # Completion
    "completion": {},
}

# Blocked Azure operations (state-modifying or dangerous)
DENIED_AZURE_COMMANDS: dict[str, dict] = {
    # Account and subscription management
    "account": {
        "set": {},
        "clear": {},
        "get-access-token": {},  # Returns sensitive tokens
    },
    # Resource lifecycle operations
    "group": {"create": {}, "delete": {}, "update": {}},
    "resource": {
        "create": {},
        "delete": {},
        "update": {},
        "move": {},
        "tag": {},
        "untag": {},
        "lock": {},
        "unlock": {},
    },
    # VM operations
    "vm": {
        "create": {},
        "delete": {},
        "start": {},
        "stop": {},
        "restart": {},
        "deallocate": {},
        "capture": {},
        "generalize": {},
        "resize": {},
        "redeploy": {},
        "reapply": {},
        "run-command": {},  # Executes commands on VMs
        "attach": {},
        "detach": {},
    },
    # Storage operations
    "storage": {
        "account": {
            "create": {},
            "delete": {},
            "update": {},
            "keys": {},
        },
        "container": {"create": {}, "delete": {}},
        "blob": {
            "upload": {},
            "download": {},
            "delete": {},
            "copy": {},
            "sync": {},
            "generate-sas": {},  # Generates access signatures
        },
        "share": {"create": {}, "delete": {}},
        "queue": {"create": {}, "delete": {}},
        "table": {"create": {}, "delete": {}},
    },
    # Network operations
    "network": {
        "vnet": {
            "create": {},
            "delete": {},
            "update": {},
            "subnet": {"create": {}, "delete": {}, "update": {}},
        },
        "nsg": {
            "create": {},
            "delete": {},
            "update": {},
            "rule": {"create": {}, "delete": {}, "update": {}},
        },
        "public-ip": {"create": {}, "delete": {}, "update": {}},
        "lb": {"create": {}, "delete": {}, "update": {}},
    },
    # AKS operations
    "aks": {
        "create": {},
        "delete": {},
        "scale": {},
        "upgrade": {},
        "rotate-certs": {},
        "get-credentials": {},  # Downloads sensitive kubeconfig
        "install-cli": {},
        "browse": {},
        "enable-addons": {},
        "disable-addons": {},
        "nodepool": {"add": {}, "delete": {}, "scale": {}, "upgrade": {}},
    },
    # Key Vault (sensitive operations)
    "keyvault": {
        "create": {},
        "delete": {},
        "purge": {},
        "recover": {},
        "set-policy": {},
        "delete-policy": {},
        "secret": {},  # All secret operations are sensitive
        "key": {},  # All key operations are sensitive
        "certificate": {},  # All certificate operations are sensitive
    },
    # App Service operations
    "webapp": {
        "create": {},
        "delete": {},
        "restart": {},
        "start": {},
        "stop": {},
        "deploy": {},
        "config": {
            "set": {},
            "appsettings": {"set": {}, "delete": {}},
            "connection-string": {"set": {}, "delete": {}},
        },
    },
    "appservice": {"plan": {"create": {}, "delete": {}, "update": {}}},
    # Database operations
    "sql": {
        "server": {"create": {}, "delete": {}, "update": {}},
        "db": {
            "create": {},
            "delete": {},
            "restore": {},
            "import": {},
            "export": {},
            "failover": {},
        },
    },
    # Authentication and authorization
    "login": {},
    "logout": {},
    "ad": {},  # Active Directory operations can be sensitive
    "role": {},  # Role assignments modify permissions
    # Extensions and configuration
    "extension": {},
    "configure": {},
    "feedback": {},
    "find": {},
    "upgrade": {},
    # Deployment and ARM operations
    "deployment": {},
    "policy": {},
    "managedapp": {},
    "feature": {},
    "provider": {},
    "snapshot": {},
    "image": {},
    "sig": {},  # Shared Image Gallery operations
    # Backup and recovery
    "backup": {},
    "restore": {},
    # CDN operations
    "cdn": {
        "profile": {"create": {}, "delete": {}},
        "endpoint": {
            "create": {},
            "delete": {},
            "purge": {},  # Purges CDN content
        },
    },
    # DevOps and CI/CD
    "devops": {},
    "repos": {},
    "artifacts": {},
    "boards": {},
    "pipelines": {},
    # Monitoring operations that expose credentials
    "monitor": {
        "log-analytics": {
            "workspace": {
                "get-shared-keys": {},  # Exposes sensitive workspace keys
            },
        },
    },
    # Function App operations
    "functionapp": {
        "invoke": {},  # Function invocation
    },
    # Batch operations
    "batch": {
        "execute": {},  # Command execution
        "job": {
            "run": {},  # Running tasks/jobs
            "submit": {},  # Job submission
            "cancel": {},  # Canceling operations
        },
    },
}
