import json
from holmes.core.supabase_dal import SupabaseDal
from holmes.config import Config
from holmes import get_version  # type: ignore
import logging


def update_holmes_status_in_db(dal: SupabaseDal, config: Config):
    logging.info("Updating status of holmes")

    if not config.cluster_name:
        raise Exception(
            "Cluster name is missing in the configuration. Please ensure 'CLUSTER_NAME' is defined in the environment variables, "
            "or verify that a cluster name is provided in the Robusta configuration file."
        )

    dal.upsert_holmes_status(
        {
            "cluster_id": config.cluster_name,
            "model": json.dumps(config.get_models_list()),
            "version": get_version(),
        }
    )
