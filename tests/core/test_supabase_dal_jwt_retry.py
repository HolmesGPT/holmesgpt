"""ROB-1580: a PostgREST call that fails with an expired JWT must be retried
with the credentials from the new sign-in, not the ones baked into the request.

Builds a real SupabaseDal + supabase client against an in-process fake
GoTrue/PostgREST (httpx.MockTransport).
"""

import base64
import concurrent.futures
import inspect
import json
import threading
import time
import uuid

import httpx
import pytest
from postgrest._sync.request_builder import (
    SyncQueryRequestBuilder,
    SyncSingleRequestBuilder,
)
from postgrest.exceptions import APIError

import holmes.core.supabase_dal as supabase_dal
from holmes.core.supabase_dal import SupabaseDal

STORE_URL = "http://supabase.test"
ANON_KEY = "anon-key"
RELAY_KEY = "relay-api-key"


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _jwt() -> str:
    payload = {"sub": "svc", "exp": int(time.time()) + 3600, "jti": uuid.uuid4().hex}
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(payload)}.{_b64('sig')}"


class FakeSupabase:
    """GoTrue + PostgREST. Tokens stay valid until `expire_all()`, which is how
    PostgREST sees them once JWT_EXPIRY passes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.valid_tokens: set = set()
        self.password_grants = 0
        self.issue_valid_tokens = True
        self.rest_error = None
        self.rest_calls: list = []
        self.token_owner: dict = {}

    def expire_all(self):
        with self.lock:
            self.valid_tokens.clear()

    @staticmethod
    def _user():
        return {
            "id": "00000000-0000-0000-0000-000000000001",
            "aud": "authenticated",
            "role": "authenticated",
            "email": "svc@example.com",
            "app_metadata": {},
            "user_metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        with self.lock:
            if path == "/auth/v1/token":
                self.password_grants += 1
                access = _jwt()
                self.token_owner[access] = json.loads(request.content)["email"]
                if self.issue_valid_tokens:
                    self.valid_tokens.add(access)
                return httpx.Response(
                    200,
                    json={
                        "access_token": access,
                        "refresh_token": uuid.uuid4().hex,
                        "token_type": "bearer",
                        "expires_in": 3600,
                        "expires_at": int(time.time()) + 3600,
                        "user": self._user(),
                    },
                )
            if path == "/auth/v1/user":
                return httpx.Response(200, json=self._user())
            if path.startswith("/rest/v1/"):
                token = request.headers.get("authorization", "").removeprefix("Bearer ")
                self.rest_calls.append((token, request.headers.get("apikey")))
                if token not in self.valid_tokens:
                    return httpx.Response(
                        401,
                        json={
                            "code": "PGRST303",
                            "message": "JWT expired",
                            "details": None,
                            "hint": None,
                        },
                    )
                if self.rest_error:
                    return httpx.Response(400, json=self.rest_error)
                if path.startswith("/rest/v1/rpc/"):
                    return httpx.Response(200, json=True)
                return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(404)


@pytest.fixture
def fake():
    return FakeSupabase()


@pytest.fixture
def relay_key():
    # What fetch_supabase_api_key returns; None falls back to the token's key.
    return {"value": None}


@pytest.fixture
def make_dal(fake, relay_key, monkeypatch):
    monkeypatch.setattr(
        supabase_dal,
        "SupabaseRetryTransport",
        lambda **_: httpx.MockTransport(fake.handle),
    )
    monkeypatch.setattr(
        supabase_dal, "fetch_supabase_api_key", lambda *_: relay_key["value"]
    )
    monkeypatch.setattr(supabase_dal, "KEY_CACHE", {})
    # Start from postgrest's own execute even if another test patched it.
    for builder in (SyncQueryRequestBuilder, SyncSingleRequestBuilder):
        monkeypatch.setattr(builder, "execute", inspect.unwrap(builder.execute))
    created = []

    def make(email="svc@example.com"):
        bundle = {
            "store_url": STORE_URL,
            "api_key": ANON_KEY,
            "account_id": "acc-1",
            "email": email,
            "password": "pw",
        }
        token = base64.b64encode(json.dumps(bundle).encode()).decode()
        monkeypatch.setenv("ROBUSTA_UI_TOKEN", token)
        created.append(SupabaseDal(cluster="test-cluster"))
        return created[-1]

    yield make
    for d in created:
        timer = d.client.auth._refresh_token_timer
        if timer:
            timer.cancel()


@pytest.fixture
def dal(make_dal):
    return make_dal()


def _bearer(dal) -> str:
    return dal.client.options.headers["Authorization"].removeprefix("Bearer ")


def test_table_query_is_retried_with_the_new_token(dal, fake):
    stale = _bearer(dal)
    fake.expire_all()

    res = dal.client.table("Issues").select("*").execute()

    assert res.data == [{"id": 1}]
    assert fake.password_grants == 2
    assert [t for t, _ in fake.rest_calls] == [stale, _bearer(dal)]


def test_rpc_is_retried_with_the_new_token(dal, fake):
    stale = _bearer(dal)
    fake.expire_all()

    res = dal.client.rpc("get_account_settings", {}).execute()

    assert res.data is True
    assert [t for t, _ in fake.rest_calls] == [stale, _bearer(dal)]


def test_retry_carries_the_api_key_of_the_new_client(dal, fake, relay_key):
    fake.expire_all()
    relay_key["value"] = RELAY_KEY

    dal.client.table("Issues").select("*").execute()

    (_, first_key), (_, retried_key) = fake.rest_calls
    assert (first_key, retried_key) == (ANON_KEY, RELAY_KEY)


def test_builder_created_before_a_reconnect_still_succeeds(dal, fake):
    query = dal.client.table("Issues").select("*")
    fake.expire_all()
    dal.sign_in()

    assert query.execute().data == [{"id": 1}]
    assert fake.password_grants == 2


def test_jwt_error_on_the_retry_is_raised_not_looped(dal, fake):
    fake.issue_valid_tokens = False
    fake.expire_all()

    with pytest.raises(APIError) as exc:
        dal.client.table("Issues").select("*").execute()

    assert exc.value.code == "PGRST303"
    assert fake.password_grants == 2
    assert len(fake.rest_calls) == 2


def test_non_jwt_errors_do_not_sign_in(dal, fake):
    fake.rest_error = {
        "code": "42P01",
        "message": "relation does not exist",
        "details": None,
        "hint": None,
    }

    with pytest.raises(APIError) as exc:
        dal.client.table("Issues").select("*").execute()

    assert exc.value.code == "42P01"
    assert fake.password_grants == 1


def test_expired_in_a_non_jwt_error_does_not_sign_in(dal, fake):
    fake.rest_error = {
        "code": "P0001",
        "message": "invitation expired",
        "details": None,
        "hint": None,
    }

    with pytest.raises(APIError) as exc:
        dal.client.rpc("accept_invitation", {}).execute()

    assert exc.value.code == "P0001"
    assert fake.password_grants == 1


def test_each_dal_retries_only_its_own_requests(make_dal, fake):
    """Every SupabaseDal patches the same class; B's request must never be
    retried with A's credentials, nor A's with B's."""
    dal_a = make_dal("a@example.com")
    dal_b = make_dal("b@example.com")
    fake.expire_all()

    dal_a.client.table("Issues").select("*").execute()
    dal_b.client.table("Issues").select("*").execute()

    owners = [fake.token_owner[t] for t, _ in fake.rest_calls]
    assert owners == ["a@example.com"] * 2 + ["b@example.com"] * 2
    assert fake.password_grants == 4


def test_concurrent_expired_requests_sign_in_once(dal, fake):
    fake.expire_all()
    start = threading.Barrier(8)

    def call(_):
        start.wait()
        return dal.client.table("Issues").select("*").execute().data

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(call, range(8)))

    assert results == [[{"id": 1}]] * 8
    assert fake.password_grants == 2
