"""Per-turn setup reads (global instructions, global and personal skills) are TTL-cached
per account / per user, with single-flight on miss (ROB-1554)."""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from holmes.core.supabase_dal import SupabaseDal
from holmes.utils.single_flight_cache import SetupTracker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


SKILL_ROW = {
    "runbook_id": "uuid-1",
    "subject_name": "Restart pods",
    "symptoms": "pods crash",
    "alerts": [],
    "clusters": None,
    "enabled": True,
}
INSTRUCTIONS_ROW = {"runbook": {"instructions": ["Always answer in English"]}}


def _fake_init_config(self):
    self.url = "https://example.supabase.co"
    self.account_id = "acct-1"
    return True


def _make_dal(monkeypatch, ttl=None):
    if ttl is not None:
        monkeypatch.setenv("SKILLS_CACHE_TTL_SEC", ttl)
    init = patch.object(SupabaseDal, "_SupabaseDal__init_config", _fake_init_config)
    connect = patch.object(SupabaseDal, "_SupabaseDal__connect")
    patch_execute = patch.object(SupabaseDal, "patch_postgrest_execute")
    with init, connect, patch_execute:
        dal = SupabaseDal(cluster="test-cluster")
    dal.client = Mock()
    return dal


def _stub(dal, data=None, error=None, delay=0.0):
    """Every query returns `data` (or raises `error`); returns the execute mock to count."""
    q = MagicMock()
    for method in ("select", "eq"):
        getattr(q, method).return_value = q

    def execute():
        if delay:
            time.sleep(delay)
        if error is not None:
            raise error
        return Mock(data=data)

    q.execute.side_effect = execute
    dal.client.table.return_value = q
    return q.execute


@pytest.fixture
def dal(monkeypatch):
    monkeypatch.delenv("SKILLS_CACHE_TTL_SEC", raising=False)
    return _make_dal(monkeypatch)


READS = {
    "global_instructions": (
        lambda d: d.get_global_instructions_for_account(),
        [INSTRUCTIONS_ROW],
    ),
    "global_skills": (lambda d: d.get_skill_catalog(), [SKILL_ROW]),
    "personal_skills": (lambda d: d.get_personal_skill_catalog("user-1"), [SKILL_ROW]),
}


@pytest.fixture(params=list(READS))
def read(request):
    return READS[request.param]


def test_two_calls_within_ttl_make_one_query(dal, read):
    fn, data = read
    execute = _stub(dal, data=data)
    first = fn(dal)
    second = fn(dal)
    assert first is not None and second == first
    assert execute.call_count == 1


def test_empty_result_is_cached(dal, read):
    fn, _ = read
    execute = _stub(dal, data=[])
    assert fn(dal) is None
    assert fn(dal) is None
    assert execute.call_count == 1


def test_call_after_ttl_expiry_requeries(dal, read):
    fn, data = read
    clock = FakeClock()
    for cache in (
        dal.global_instructions_cache,
        dal.global_skills_cache,
        dal.personal_skills_cache,
    ):
        cache.timer = clock
    execute = _stub(dal, data=data)
    fn(dal)
    clock.now += 59
    fn(dal)
    assert execute.call_count == 1
    clock.now += 2
    fn(dal)
    assert execute.call_count == 2


def test_twenty_threads_on_cold_cache_make_one_query(dal, read):
    fn, data = read
    execute = _stub(dal, data=data, delay=0.2)
    barrier = threading.Barrier(20)
    results = []

    def worker():
        barrier.wait()
        results.append(fn(dal))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == 20 and all(r is not None for r in results)
    assert execute.call_count == 1


def test_query_failure_returns_none_and_is_not_cached(dal, read):
    fn, data = read
    _stub(dal, error=RuntimeError("supabase down"))
    assert fn(dal) is None
    execute = _stub(dal, data=data)
    assert fn(dal) is not None
    assert execute.call_count == 1


def test_invalidate_setup_caches_forces_requery(dal, read):
    fn, data = read
    execute = _stub(dal, data=data)
    fn(dal)
    dal.invalidate_setup_caches()
    fn(dal)
    assert execute.call_count == 2


def test_disabled_dal_never_queries(dal, read):
    fn, data = read
    execute = _stub(dal, data=data)
    dal.enabled = False
    assert fn(dal) is None
    assert execute.call_count == 0


def test_personal_skills_are_cached_per_user(dal):
    execute = _stub(dal, data=[SKILL_ROW])
    dal.get_personal_skill_catalog("user-1")
    dal.get_personal_skill_catalog("user-2")
    dal.get_personal_skill_catalog("user-1")
    assert execute.call_count == 2
    user_filters = [
        c.args[1]
        for c in dal.client.table.return_value.eq.call_args_list
        if c.args[0] == "user_id"
    ]
    assert user_filters == ["user-1", "user-2"]


def test_personal_skills_without_user_id_skip_query_and_cache(dal):
    execute = _stub(dal, data=[SKILL_ROW])
    assert dal.get_personal_skill_catalog("") is None
    assert dal.get_personal_skill_catalog(None) is None  # type: ignore[arg-type]
    assert execute.call_count == 0


def test_reads_do_not_share_cache_entries(dal):
    """All three read HolmesRunbooks; a shared key would serve one's rows as another's."""
    q = MagicMock()
    for method in ("select", "eq"):
        getattr(q, method).return_value = q
    q.execute.side_effect = [
        Mock(data=[INSTRUCTIONS_ROW]),
        Mock(data=[SKILL_ROW]),
        Mock(data=[dict(SKILL_ROW, runbook_id="uuid-personal")]),
    ]
    dal.client.table.return_value = q
    instructions = dal.get_global_instructions_for_account()
    global_skills = dal.get_skill_catalog()
    personal = dal.get_personal_skill_catalog("user-1")
    assert instructions.instructions == ["Always answer in English"]
    assert [s.id for s in global_skills] == ["uuid-1"]
    assert [s.id for s in personal] == ["uuid-personal"]


def test_setup_tracker_sees_hits_and_misses(dal):
    _stub(dal, data=[SKILL_ROW])
    setup = SetupTracker()
    with setup.track():
        dal.get_skill_catalog()
        dal.get_skill_catalog()
        dal.get_personal_skill_catalog("user-1")
    assert (setup.stats.hits, setup.stats.misses) == (1, 2)


@pytest.mark.parametrize("raw", ["abc", "-5", "1.5", ""])
def test_invalid_ttl_env_falls_back_to_default(monkeypatch, raw):
    dal = _make_dal(monkeypatch, ttl=raw)
    assert dal.global_skills_cache.ttl == 60
    assert dal.personal_skills_cache.ttl == 60
    assert dal.global_instructions_cache.ttl == 60


def test_ttl_env_is_applied(monkeypatch):
    dal = _make_dal(monkeypatch, ttl="5")
    assert dal.global_skills_cache.ttl == 5


def test_zero_ttl_disables_caching(monkeypatch):
    dal = _make_dal(monkeypatch, ttl="0")
    execute = _stub(dal, data=[SKILL_ROW])
    dal.get_skill_catalog()
    dal.get_skill_catalog()
    assert execute.call_count == 2


def test_zero_hierarchy_ttl_still_rejected(monkeypatch):
    monkeypatch.setenv("SKILL_HIERARCHY_CACHE_TTL_SEC", "0")
    dal = _make_dal(monkeypatch)
    assert dal.skill_hierarchy_cache.ttl == 60


def test_invalidate_on_disabled_dal_is_a_noop():
    """Holmes without a platform token builds a disabled DAL, and /api/admin/reload still
    calls invalidate_setup_caches on it."""
    with patch.object(SupabaseDal, "_SupabaseDal__init_config", return_value=False):
        dal = SupabaseDal(cluster="test-cluster")
    assert dal.enabled is False
    dal.invalidate_setup_caches()
    assert dal.get_skill_catalog() is None
    assert dal.get_personal_skill_catalog("user-1") is None
    assert dal.get_global_instructions_for_account() is None
