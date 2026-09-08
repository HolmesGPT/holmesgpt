"""Opt-in capture of every eval answer, pass or fail.

pytest prints only FAILING answers, so any before/after comparison scraped
from a run log is silently biased toward failures — a measurement over that
population can move purely because the pass rate moved. Set ANSWER_DUMP_DIR
to write every answer to its own file instead, giving the whole population.

Off unless ANSWER_DUMP_DIR is set, so normal runs are unaffected.

    ANSWER_DUMP_DIR=/tmp/before poetry run pytest -k my_eval --no-cov
"""

import os
import re
import uuid
from typing import Any

_ENV_VAR = "ANSWER_DUMP_DIR"


def dump_eval_answer(test_id: str, output: Any, correctness: Any) -> None:
    """Write one answer to ANSWER_DUMP_DIR, named by verdict. No-op when unset.

    Never raises: a measurement aid must not be able to fail a test run."""
    directory = os.environ.get(_ENV_VAR)
    if not directory:
        return
    try:
        os.makedirs(directory, exist_ok=True)
        verdict = "pass" if int(correctness or 0) == 1 else "fail"
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(test_id))[:80]
        path = os.path.join(
            directory, f"{verdict}-{safe_id}-{uuid.uuid4().hex[:8]}.txt"
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(output))
    except Exception:  # noqa: BLE001 - never break a run over a debug aid
        pass
