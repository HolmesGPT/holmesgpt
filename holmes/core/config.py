import os

config_path_dir: str = os.environ.get(
    "HOLMES_CONFIGPATH_DIR", os.path.expanduser("~/.holmes")
)
