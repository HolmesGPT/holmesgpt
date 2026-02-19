import os
from typing import Optional

# NOTE: This one will be mounted if openshift is enabled in values.yaml
TOKEN_LOCATION = os.environ.get(
    "TOKEN_LOCATION", "/var/run/secrets/kubernetes.io/serviceaccount/token"
)


def load_openshift_token() -> Optional[str]:
    try:
        with open(TOKEN_LOCATION, "r") as file:
            return file.read()
    except FileNotFoundError:
        return None
