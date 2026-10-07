"""ROB-1555: re-login after a JWT error is single-flight.

Runs the real supabase/postgrest/gotrue clients against an in-process fake
Supabase (httpx.MockTransport), so the patched ``execute`` paths for table
queries and rpc() are exercised exactly as in production.
"""

import base64
import json
import threading
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import httpx
import pytest
from postgrest._sync.request_builder import (
    SyncQueryRequestBuilder,
    SyncSingleRequestBuilder,
)
from postgrest.exceptions import APIError

from holmes.core import supabase_dal
from holmes.core.supabase_dal import KEY_CACHE, SupabaseDal
from holmes.utils.holmes_status import update_holmes_status_in_db

STORE_URL = "https://fake.supabase.test"
API_KEY = "anon-key"


def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _jwt(n: int) -> str:
    payload = {"sub": "user-1", "exp": int(time.time()) + 3600, "n": n}
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(payload)}.sig{n}"


class FakeSupabase:
    """Issues tok-1, tok-2, ... on each password sign-in. REST calls made with a
    token in ``expired`` (or an unknown one) get PGRST301."""

    def __init__(self):
        self.lock = threading.Lock()
        self.issued: list[str] = []
        self.expired: set[str] = set()
        self.sign_in_delay = 0.0
        self.sign_in_status = 200
        self.sign_in_gate: Optional[threading.Event] = None
        self.sign_in_started = threading.Event()
        self.reject_barrier: Optional[threading.Barrier] = None
        self.rest_auth_headers: list[str] = []

    @property
    def sign_ins(self) -> int:
        return len(self.issued)

    def expire_all(self) -> None:
        self.expired.update(self.issued)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/v1/token":
            return self._token()
        if path == "/auth/v1/user":
            return httpx.Response(200, json=_user())
        if path.startswith("/rest/v1/"):
            return self._rest(request)
        return httpx.Response(404, json={"message": f"unexpected {path}"})

    def _token(self) -> httpx.Response:
        self.sign_in_started.set()
        if self.sign_in_gate is not None:
            self.sign_in_gate.wait(5)
        time.sleep(self.sign_in_delay)
        if self.sign_in_status != 200:
            return httpx.Response(self.sign_in_status, json={"msg": "auth down"})
        with self.lock:
            token = _jwt(len(self.issued) + 1)
            self.issued.append(token)
        return httpx.Response(
            200,
            json={
                "access_token": token,
                "refresh_token": f"refresh-{len(self.issued)}",
                "expires_in": 3600,
                "expires_at": int(time.time()) + 3600,
                "token_type": "bearer",
                "user": _user(),
            },
        )

    def _rest(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        with self.lock:
            self.rest_auth_headers.append(auth)
        token = auth.removeprefix("Bearer ")
        if token in self.expired or token not in self.issued:
            if self.reject_barrier is not None:
                try:
                    self.reject_barrier.wait(5)
                except threading.BrokenBarrierError:
                    pass
            return _pg_error(401, "PGRST301", "JWT expired")
        if "/rpc/" in request.url.path:
            return httpx.Response(200, json=True)
        return httpx.Response(200, json=[{"id": 1}])


def _pg_error(status: int, code: str, message: str) -> httpx.Response:
    body = {"code": code, "message": message, "details": None, "hint": None}
    return httpx.Response(status, json=body)


def _user() -> dict:
    return {
        "id": "user-1",
        "aud": "authenticated",
        "role": "authenticated",
        "email": "svc@example.com",
        "app_metadata": {},
        "user_metadata": {},
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def fake() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def dal(monkeypatch, fake):
    token = {
        "store_url": STORE_URL,
        "api_key": API_KEY,
        "account_id": "acc-1",
        "email": "svc@example.com",
        "password": "pw",
    }
    monkeypatch.setenv(
        "ROBUSTA_UI_TOKEN", base64.b64encode(json.dumps(token).encode()).decode()
    )
    KEY_CACHE.clear()
    http_client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    with patch.object(supabase_dal, "fetch_supabase_api_key", return_value=None):
        with patch.object(supabase_dal.httpx, "Client", return_value=http_client):
            dal = SupabaseDal(cluster="c1")
        yield dal
    for builder, original in getattr(supabase_dal, "_ORIGINAL_EXECUTES", {}).items():
        builder.execute = original  # type: ignore[method-assign]
    KEY_CACHE.clear()


def _table(dal):
    return dal.client.table("HolmesStatus").select("*").execute()


def _rpc(dal):
    return dal.client.rpc("is_realtime_enabled", {}).execute()


def _run_concurrently(n: int, fn):
    results: list = [None] * n
    start = threading.Barrier(n)

    def run(i):
        start.wait()
        try:
            results[i] = fn(i)
        except Exception as e:
            results[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    return results


@pytest.mark.parametrize("query", [_table, _rpc], ids=["table", "rpc"])
def test_concurrent_jwt_expiry_signs_in_once_and_all_requests_succeed(dal, fake, query):
    n = 20
    assert fake.sign_ins == 1
    fake.expire_all()
    fake.reject_barrier = threading.Barrier(n)
    fake.sign_in_delay = 0.2

    results = _run_concurrently(n, lambda _i: query(dal))

    errors = [r for r in results if isinstance(r, Exception)]
    assert errors == []
    assert fake.sign_ins == 2  # startup + exactly one re-login
    assert dal.relogin_count == 1


def test_mixed_table_and_rpc_storm_signs_in_once(dal, fake):
    n = 20
    fake.expire_all()
    fake.reject_barrier = threading.Barrier(n)
    fake.sign_in_delay = 0.2

    results = _run_concurrently(n, lambda i: _table(dal) if i % 2 else _rpc(dal))

    assert [r for r in results if isinstance(r, Exception)] == []
    assert fake.sign_ins == 2


def test_retry_is_sent_with_the_new_token(dal, fake):
    old = fake.issued[-1]
    fake.expire_all()

    assert _table(dal).data == [{"id": 1}]

    new = fake.issued[-1]
    assert new != old
    assert fake.rest_auth_headers == [f"Bearer {old}", f"Bearer {new}"]


def test_request_built_before_a_relogin_retries_without_signing_in_again(dal, fake):
    stale_request = dal.client.table("HolmesStatus").select("*")
    fake.expire_all()
    _table(dal)  # another thread's request triggers the re-login
    assert fake.sign_ins == 2

    assert stale_request.execute().data == [{"id": 1}]
    assert fake.sign_ins == 2
    assert dal.relogin_count == 1


def test_in_place_sign_in_by_realtime_is_not_followed_by_another_relogin(dal, fake):
    stale_request = dal.client.rpc("is_realtime_enabled", {})
    fake.expire_all()
    dal.sign_in()  # what RealtimeManager does near JWT expiry

    assert stale_request.execute().data is True
    assert fake.sign_ins == 2
    assert dal.relogin_count == 0


def test_client_is_swapped_only_after_the_new_one_is_signed_in(dal, fake):
    old_client = dal.client
    old_auth = old_client.postgrest.headers["Authorization"]
    fake.expire_all()
    fake.sign_in_gate = threading.Event()
    fake.sign_in_started.clear()

    relogin = threading.Thread(target=_table, args=(dal,))
    relogin.start()
    assert fake.sign_in_started.wait(5)
    try:
        # Mid re-login: everyone else still sees the old, fully signed-in client.
        assert dal.client is old_client
        assert dal.client.postgrest.headers["Authorization"] == old_auth
        assert old_auth != f"Bearer {API_KEY}"
    finally:
        fake.sign_in_gate.set()
        relogin.join(5)

    assert dal.client is not old_client
    assert dal.client.postgrest.headers["Authorization"] == f"Bearer {fake.issued[-1]}"


def test_failed_relogin_fails_fast_for_waiters_and_recovers_later(dal, fake):
    n = 5
    fake.expire_all()
    fake.reject_barrier = threading.Barrier(n)
    fake.sign_in_status = 500
    fake.sign_in_delay = 0.3
    attempts_before = dal._relogin_attempts

    results = _run_concurrently(n, lambda _i: _table(dal))

    assert all(isinstance(r, Exception) for r in results)
    assert dal._relogin_attempts == attempts_before + 1
    assert dal.relogin_count == 0
    assert dal._reconnect_lock.acquire(blocking=False)
    dal._reconnect_lock.release()

    fake.reject_barrier = None
    fake.sign_in_status = 200
    fake.sign_in_delay = 0
    assert _table(dal).data == [{"id": 1}]
    assert dal.relogin_count == 1


def test_each_later_request_retries_a_failed_relogin(dal, fake):
    fake.expire_all()
    fake.sign_in_status = 500
    with pytest.raises(Exception):
        _table(dal)
    with pytest.raises(Exception):
        _table(dal)
    assert dal._relogin_attempts == 2


def test_jwt_error_on_the_retry_is_raised_without_looping(dal, fake):
    fake.expire_all()
    original_token = fake._token

    def token_then_expire():
        resp = original_token()
        fake.expire_all()
        return resp

    fake._token = token_then_expire  # type: ignore[method-assign]
    with pytest.raises(APIError) as exc:
        _table(dal)
    assert exc.value.code == "PGRST301"
    assert fake.sign_ins == 2


def test_non_jwt_errors_pass_through_without_relogin(dal, fake):
    def bad_request(request):
        if request.url.path.startswith("/rest/v1/"):
            return _pg_error(400, "PGRST100", "bad filter")
        return fake.handler(request)

    dal.client.postgrest.session._transport = httpx.MockTransport(bad_request)
    with pytest.raises(APIError) as exc:
        _table(dal)
    assert exc.value.code == "PGRST100"
    assert fake.sign_ins == 1


def test_expired_message_without_pgrst301_also_relogs_in(dal, fake):
    calls = {"n": 0}

    def expired_once(request):
        if request.url.path.startswith("/rest/v1/") and calls["n"] == 0:
            calls["n"] += 1
            return _pg_error(401, "PGRST303", "JWT expired")
        return fake.handler(request)

    dal.client.postgrest.session._transport = httpx.MockTransport(expired_once)
    assert _table(dal).data == [{"id": 1}]
    assert dal.relogin_count == 1


def test_patching_twice_does_not_stack_wrappers(dal, fake):
    dal.patch_postgrest_execute()
    fake.expire_all()

    assert _table(dal).data == [{"id": 1}]
    assert fake.sign_ins == 2
    assert len(fake.rest_auth_headers) == 2


def test_superseded_client_stops_refreshing_its_token(dal, fake):
    old_client = dal.client
    assert old_client.auth._refresh_token_timer is not None
    fake.expire_all()

    _table(dal)

    assert old_client.auth._refresh_token_timer is None
    assert dal.client.auth._refresh_token_timer is not None


def test_stop_auto_refresh_tolerates_unexpected_client_shape():
    supabase_dal._stop_auto_refresh(object())  # type: ignore[arg-type]


def test_relay_key_is_fetched_once_per_relogin(dal, fake):
    fetches = []

    def fetch(*_args):
        fetches.append(1)
        return None

    n = 10
    fake.expire_all()
    fake.reject_barrier = threading.Barrier(n)
    fake.sign_in_delay = 0.1
    with patch.object(supabase_dal, "fetch_supabase_api_key", side_effect=fetch):
        results = _run_concurrently(n, lambda _i: _table(dal))

    assert [r for r in results if isinstance(r, Exception)] == []
    assert len(fetches) == 1


def test_patched_execute_covers_both_builders(dal):
    originals = supabase_dal._ORIGINAL_EXECUTES
    assert SyncQueryRequestBuilder.execute is not originals[SyncQueryRequestBuilder]
    assert SyncSingleRequestBuilder.execute is not originals[SyncSingleRequestBuilder]


def test_heartbeat_reports_relogin_count(dal, fake):
    fake.expire_all()
    _table(dal)

    upserted = {}
    dal.upsert_holmes_status = upserted.update  # type: ignore[method-assign]
    config = MagicMock(cluster_name="c1", should_try_robusta_ai=False)
    config.get_models_list.return_value = []
    config.llm_model_registry.reads_robusta_catalog.return_value = True

    update_holmes_status_in_db(dal, config)

    assert json.loads(upserted["metadata"])["supabase_relogins"] == 1
