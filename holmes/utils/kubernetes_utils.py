from typing import Optional

import certifi
from kubernetes import client, config


def load_kube_config_with_certifi(config_file: Optional[str] = None) -> None:
    configuration = client.Configuration()
    config.load_kube_config(config_file=config_file, client_configuration=configuration)
    # SDK 32 used certifi when the kubeconfig did not supply its own CA.
    if not configuration.ssl_ca_cert:
        configuration.ssl_ca_cert = certifi.where()
    client.Configuration.set_default(configuration)
