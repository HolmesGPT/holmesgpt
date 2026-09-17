"""Opt-in capture of every eval answer, pass or fail.

pytest prints only FAILING answers, so any before/after comparison scraped
from a run log is silently biased toward failures — a measurement over that
population can move purely because the pass rate moved. Set ANSWER_DUMP_DIR
to write every answer to its own file instead, giving the whole population.

Answers land under one directory per model and env config:

    $ANSWER_DUMP_DIR/<model>/<env_config>/<verdict>-<test_id>-<rand>.txt

Attribution is by directory, not by substring of a filename, and deliberately
so: a scorer that filtered `-gpt-4.1-` out of flat filenames also matched
`-gpt-4.1-mini-` and silently pooled two models, which invalidated a
measurement on this very change. Path segments cannot be confused that way.
Each file also carries its own `model:` / `env_config:` header lines.

Off unless ANSWER_DUMP_DIR is set, so normal runs are unaffected.

    ANSWER_DUMP_DIR=/tmp/before poetry run pytest -k my_eval --no-cov
"""

import hashlib
import os
import re
import uuid
from typing import Any

_ENV_VAR = "ANSWER_DUMP_DIR"
_HEADER_END = "--- answer ---"


def _segment(value: Any, fallback: str) -> str:
    """One safe path segment: no separators, no traversal, bounded length.

    Sanitising is lossy ("a/b" and "a_b" both read a_b), so whenever it changed
    the value a short digest of the original is appended. Identifiers that were
    already safe, like `gpt-4.1`, keep their exact name."""
    original = str(value)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", original)[:72].strip(".")
    if not safe:
        return fallback
    if safe != original:
        safe += "-" + hashlib.sha1(original.encode("utf-8")).hexdigest()[:6]
    return safe


def dump_eval_answer(
    test_id: str,
    output: Any,
    correctness: Any,
    model: Any = "",
    env_config: Any = "",
    tools: Any = (),
) -> None:
    """Write one answer under ANSWER_DUMP_DIR. No-op when the var is unset.

    Never raises: a measurement aid must not be able to fail a test run."""
    directory = os.environ.get(_ENV_VAR)
    if not directory:
        return
    try:
        target = os.path.join(
            directory,
            _segment(model, "unknown-model"),
            _segment(env_config, "default"),
        )
        os.makedirs(target, exist_ok=True)
        verdict = "pass" if int(correctness or 0) == 1 else "fail"
        name = (
            f"{verdict}-{_segment(test_id, 'unknown-test')}-{uuid.uuid4().hex[:8]}.txt"
        )
        payload = (
            f"test_id: {test_id}\n"
            f"model: {model}\n"
            f"env_config: {env_config}\n"
            f"verdict: {verdict}\n"
            f"tools: {', '.join(str(t) for t in tools)}\n"
            f"{_HEADER_END}\n"
            f"{output!s}"
        )
        with open(os.path.join(target, name), "w", encoding="utf-8") as handle:
            handle.write(payload)
    except Exception:  # noqa: BLE001 - never break a run over a debug aid
        pass
