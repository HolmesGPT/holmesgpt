"""ROB-1555 integration: concurrent requests that hit an invalid Supabase JWT
re-login once and all succeed, against the real Supabase backend.

Requires ROBUSTA_UI_TOKEN (and CLUSTER_NAME). Run:
    poetry run pytest tests/core/conversations_worker/integration/test_jwt_relogin.py \
        -m conversation_worker --no-cov -v
"""

import os
import threading
from unittest.mock import patch

import pytest

from holmes.core import supabase_dal
from holmes.core.supabase_dal import SupabaseDal

pytestmark = [pytest.mark.conversation_worker, pytest.mark.integration]

THREADS = 20


@pytest.fixture
def dal():
    if not os.environ.get("ROBUSTA_UI_TOKEN"):
        pytest.skip("ROBUSTA_UI_TOKEN not set")
    dal = SupabaseDal(os.environ.get("CLUSTER_NAME", "rob1555-integration"))
    if not dal.enabled:
        pytest.skip("Supabase DAL not enabled")
    yield dal
    for builder, original in supabase_dal._ORIGINAL_EXECUTES.items():
        setattr(builder, "execute", original)


def _corrupt_jwt(dal: SupabaseDal) -> None:
    token = dal.client.postgrest.headers["Authorization"].removeprefix("Bearer ")
    dal.client.postgrest.auth(token[:-4] + "AAAA")


def test_concurrent_invalid_jwt_relogs_in_once(dal):
    def query(i):
        if i % 2:
            return dal.client.rpc("is_realtime_enabled", {}).execute()
        return (
            dal.client.table("HolmesStatus")
            .select("cluster_id")
            .eq("account_id", dal.account_id)
            .execute()
        )

    expected_rows = len(query(0).data)
    _corrupt_jwt(dal)

    results: list = [None] * THREADS
    start = threading.Barrier(THREADS)

    def run(i):
        start.wait()
        try:
            results[i] = query(i)
        except Exception as e:
            results[i] = e

    real_sign_in = SupabaseDal.sign_in
    with patch.object(
        SupabaseDal, "sign_in", autospec=True, side_effect=real_sign_in
    ) as sign_in:
        threads = [threading.Thread(target=run, args=(i,)) for i in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)

    assert [r for r in results if isinstance(r, Exception)] == []
    assert all(len(r.data) == expected_rows for r in results[0::2])
    assert sign_in.call_count == 1
    assert dal.relogin_count == 1
