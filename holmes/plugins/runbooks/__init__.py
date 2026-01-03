import json
import logging
import os
import os.path
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Pattern, Tuple, Union

import yaml
from pydantic import BaseModel, PrivateAttr

from holmes.utils.pydantic_utils import RobustaBaseConfig, load_model_from_file

if TYPE_CHECKING:
    from holmes.core.supabase_dal import SupabaseDal

THIS_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_RUNBOOK_SEARCH_PATH = THIS_DIR

CATALOG_FILE = "catalog.json"


class RobustaRunbookInstruction(BaseModel):
    id: str
    symptom: str
    title: str
    instruction: Optional[str] = None
    alerts: List[str] = []

    """
    Custom YAML dumper to represent multi-line strings in literal block style due to instructions often being multi-line.
    for example:
    instructions: |
      Step 1: Do this
      Step 2: Do that

    instead of:
    instructions: "Step 1: Do this
    Step 2: Do that"

    """

    class _LiteralDumper(yaml.SafeDumper):
        pass

    @staticmethod
    def _repr_str(dumper, s: str):
        s = s.replace("\\n", "\n")
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", s, style="|" if "\n" in s else None
        )

    _LiteralDumper.add_representer(str, _repr_str)  # type: ignore

    def to_list_string(self) -> str:
        return f"{self.id}"

    def to_prompt_string(self) -> str:
        return f"id='{self.id}' | title='{self.title}' | symptom='{self.symptom}' | relevant alerts={', '.join(self.alerts)}"

    def pretty(self) -> str:
        try:
            data = self.model_dump(exclude_none=True)  # pydantic v2
        except AttributeError:
            data = self.dict(exclude_none=True)  # pydantic v1
        return yaml.dump(
            data, Dumper=self._LiteralDumper, sort_keys=False, allow_unicode=True
        )


class IssueMatcher(RobustaBaseConfig):
    issue_id: Optional[Pattern] = None  # unique id
    issue_name: Optional[Pattern] = None  # not necessary unique
    source: Optional[Pattern] = None


class Runbook(RobustaBaseConfig):
    match: IssueMatcher
    instructions: str

    _path = PrivateAttr()

    def set_path(self, path: str):
        self._path = path

    def get_path(self) -> str:
        return self._path


class ListOfRunbooks(BaseModel):
    runbooks: List[Runbook]


def load_runbooks_from_file(path: Union[str, Path]) -> List[Runbook]:
    data: ListOfRunbooks = load_model_from_file(ListOfRunbooks, file_path=path)  # type: ignore
    for runbook in data.runbooks:
        runbook.set_path(path)  # type: ignore
    return data.runbooks


def load_builtin_runbooks() -> List[Runbook]:
    all_runbooks = []
    for filename in os.listdir(THIS_DIR):
        if not filename.endswith(".yaml"):
            continue
        path = os.path.join(THIS_DIR, filename)
        all_runbooks.extend(load_runbooks_from_file(path))
    return all_runbooks


class RunbookCatalogEntry(BaseModel):
    """
    RunbookCatalogEntry contains metadata about a runbook
    Different from runbooks provided by Runbook class, this entry points to markdown file containing the runbook content.
    """

    id: str
    update_date: date
    description: str
    link: str

    def to_list_string(self) -> str:
        return f"{self.link}"

    def to_prompt_string(self) -> str:
        return f"{self.link} | description: {self.description}"


class RunbookCatalog(BaseModel):
    catalog: List[Union[RunbookCatalogEntry, "RobustaRunbookInstruction"]]  # type: ignore

    def list_available_runbooks(self) -> list[str]:
        return [entry.to_list_string() for entry in self.catalog]

    def split_by_type(
        self,
    ) -> Tuple[List[RunbookCatalogEntry], List[RobustaRunbookInstruction]]:
        md: List[RunbookCatalogEntry] = []
        robusta: List[RobustaRunbookInstruction] = []  #
        for catalog_entry in self.catalog:
            if isinstance(catalog_entry, RunbookCatalogEntry):
                md.append(catalog_entry)
            elif isinstance(catalog_entry, RobustaRunbookInstruction):
                robusta.append(catalog_entry)
        return md, robusta

    def to_prompt_string(self) -> str:
        md, robusta = self.split_by_type()
        parts: List[str] = [""]
        if md:
            parts.append("Here are MD runbooks:")
            parts.extend(f"* {e.to_prompt_string()}" for e in md)
        if robusta:
            parts.append("\nHere are Robusta runbooks:")
            parts.extend(f"* {e.to_prompt_string()}" for e in robusta)
        return "\n".join(parts)


def load_runbook_catalog(
    dal: Optional["SupabaseDal"] = None,
    custom_catalog_paths: Optional[List[Union[str, Path]]] = None,
) -> Optional[RunbookCatalog]:  # type: ignore
    dir_path = os.path.dirname(os.path.realpath(__file__))
    catalog = None
    catalogPath = os.path.join(dir_path, CATALOG_FILE)
    try:
        if os.path.isfile(catalogPath):
            with open(catalogPath) as file:
                catalog_dict = json.load(file)
                catalog = RunbookCatalog(**catalog_dict)
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from {catalogPath}: {e}")
    except Exception as e:
        logging.error(
            f"Unexpected error while loading runbook catalog from {catalogPath}: {e}"
        )

    # Append custom catalog files if provided
    if custom_catalog_paths:
        for custom_catalog_path in custom_catalog_paths:
            try:
                custom_catalog_path_str = str(custom_catalog_path)
                if not os.path.isfile(custom_catalog_path_str):
                    logging.warning(
                        f"Custom catalog file not found: {custom_catalog_path_str}"
                    )
                    continue

                with open(custom_catalog_path_str) as file:
                    custom_catalog_dict = json.load(file)
                    custom_catalog = RunbookCatalog(**custom_catalog_dict)

                    if catalog:
                        catalog.catalog.extend(custom_catalog.catalog)
                    else:
                        catalog = custom_catalog
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON from {custom_catalog_path}: {e}")
            except Exception as e:
                logging.error(
                    f"Unexpected error while loading custom catalog from {custom_catalog_path}: {e}"
                )

    # Append additional runbooks from SupabaseDal if provided
    if dal:
        try:
            supabase_entries = dal.get_runbook_catalog()
            if not supabase_entries:
                return catalog
            if catalog:
                catalog.catalog.extend(supabase_entries)
            else:
                # if failed to load from file, create new catalog from supabase
                catalog = RunbookCatalog(catalog=supabase_entries)  # type: ignore
        except Exception as e:
            logging.error(f"Error loading runbooks from Supabase: {e}")

    return catalog


def get_runbook_by_path(
    runbook_relative_path: str, search_paths: List[str]
) -> Optional[str]:
    """
    Find a runbook by searching through provided paths.

    Args:
        runbook_relative_path: The relative path to the runbook
        search_paths: Optional list of directories to search. If None, uses default runbook folder.

    Returns:
        Full path to the runbook if found, None otherwise
    """
    # Validate runbook_relative_path is not empty
    if not runbook_relative_path or not runbook_relative_path.strip():
        return None

    for search_path in search_paths:
        runbook_path = os.path.join(search_path, runbook_relative_path)
        # Ensure it's a file, not a directory
        if os.path.isfile(runbook_path):
            return runbook_path

    return None
