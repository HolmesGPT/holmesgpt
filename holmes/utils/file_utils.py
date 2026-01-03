import json
import logging
import os

import yaml  # type: ignore


def write_json_file(json_output_file: str, json_ob_to_dump):
    try:
        dirname = os.path.dirname(json_output_file)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(json_output_file, "w", encoding="utf-8") as f:
            json.dump(json_ob_to_dump, f, ensure_ascii=False, indent=4, default=str)
    except Exception:
        logging.exception("Failed to create the json file.")
        return


def load_yaml_file(
    path: str, raise_error: bool = True, warn_not_found: bool = True
) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as file:
            parsed_yaml = yaml.safe_load(file)
    except yaml.YAMLError as err:
        logging.warning(f"Error parsing YAML from {path}: {err}")
        if raise_error:
            raise err
        return {}
    except FileNotFoundError as err:
        if warn_not_found:
            logging.warning(f"file {path} was not found.")
        if raise_error:
            raise err
        return {}
    except Exception as err:
        logging.warning(f"Failed to open file {path}: {err}")
        if raise_error:
            raise err
        return {}

    if not parsed_yaml:
        message = f"No content found in file: {path}"
        logging.warning(message)
        if raise_error:
            raise ValueError(message)
        return {}

    if not isinstance(parsed_yaml, dict):
        message = f"Invalid format: YAML file {path} does not contain a dictionary at the root."
        logging.warning(message)
        if raise_error:
            raise ValueError(message)
        return {}

    return parsed_yaml
