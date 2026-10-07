"""Load the human-validated incident corpus from disk.

Corpus entries are YAML files, one incident each, so that adding an incident is
a reviewable diff rather than a row in an opaque blob. Loading is strict: a file
that does not parse raises instead of being skipped, because a silently dropped
incident changes eval results without changing any number a reader would notice.
"""

from pathlib import Path
from typing import List, Optional, Sequence

import yaml

from holmes.core.investigation_path.schema import IncidentRecord

DEFAULT_CORPUS_DIR = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "investigation_path" / "corpus"
)


def load_corpus(
    directory: Optional[Path] = None,
    split: Optional[str] = None,
) -> List[IncidentRecord]:
    """Read every incident YAML in `directory`, optionally filtered by split.

    Records come back sorted by incident id so eval runs are reproducible.
    """
    directory = Path(directory) if directory else DEFAULT_CORPUS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"Incident corpus directory not found: {directory}")

    records: List[IncidentRecord] = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as handle:
            payload = yaml.safe_load(handle)
        if payload is None:
            raise ValueError(f"Empty incident file: {path}")
        try:
            records.append(IncidentRecord.model_validate(payload))
        except ValueError as e:
            raise ValueError(f"Invalid incident record in {path}: {e}") from e

    if split is not None:
        records = [record for record in records if record.split == split]
    records.sort(key=lambda record: record.incident_id)
    return records


def corpus_bytes_per_incident(records: Sequence[IncidentRecord]) -> float:
    """Mean serialized size of a stored record, for the storage-cost metric."""
    if not records:
        return 0.0
    total = sum(len(record.model_dump_json().encode("utf-8")) for record in records)
    return total / len(records)
