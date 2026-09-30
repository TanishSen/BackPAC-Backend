"""The doors: who may call what, and how often.

These go through the real app and its routing, with the settings and the
database swapped out, so nothing here touches Supabase, LiveKit or the agent.
What they pin down is the part that only fails in production — a route that
is open when it should be shut, or shut to the one caller that needs it.
"""

import httpx
import pytest
import respx

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.domains.trips.router import search_limiter
from app.domains.voice.router import voice_limiter
from app.main import app
from app.shared.rate_limit import RateLimiter

TOKEN = "test-service-token"
AGENT = "http://agent.test"

SETTINGS = Settings(
    _env_file=None,
    service_token=TOKEN,
    supabase_project_ref="testproject",
    agent_base_url=AGENT,
    travelpayouts_token="",
)


async def _no_db():
    yield None


@pytest.fixture
async def client():
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_db_session] = _no_db
    search_limiter.reset()
    voice_limiter.reset()
    async with httpx.AsyncClient() as agent_http:
        # The lifespan is not run here (it would connect to the database), so
        # the shared client it normally creates is put in place by hand.
        app.state.http_client = agent_http
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as c:
            yield c
    app.dependency_overrides.clear()


TRAINS = {"origin": "Delhi", "destination": "Jaipur", "departDate": "2026-12-25"}


# --- trip search ---------------------------------------------------------


async def test_search_is_shut_to_anonymous_callers(client):
    # Each search can spend the flight API's shared hourly budget.
    r = await client.post("/api/v1/trips/search/trains", json=TRAINS)
    assert r.status_code == 401


async def test_search_refuses_a_wrong_service_token(client):
    r = await client.post(
        "/api/v1/trips/search/trains",
        json=TRAINS,
        headers={"X-Service-Token": "nope"},
    )
    assert r.status_code == 401


async def test_the_agent_can_search_with_its_token(client):
    r = await client.post(
        "/api/v1/trips/search/trains",
        json=TRAINS,
        headers={"X-Service-Token": TOKEN},
    )
    assert r.status_code == 200
    assert r.json()[0]["provider"] == "IRCTC"


async def test_the_agent_is_not_rate_limited_on_search(client):
    # It searches for every call in progress at once.
    for _ in range(search_limiter.limit + 5):
        r = await client.post(
            "/api/v1/trips/search/trains",
            json=TRAINS,
            headers={"X-Service-Token": TOKEN},
        )
        assert r.status_code == 200


async def test_search_rejects_unbounded_input(client):
    r = await client.post(
        "/api/v1/trips/search/trains",
        json={**TRAINS, "origin": "x" * 500},
        headers={"X-Service-Token": TOKEN},
    )
    assert r.status_code == 422


# --- saved trips and sessions --------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/trips/saved"),
        ("GET", "/api/v1/trips/saved/someone-else"),
        ("POST", "/api/v1/trips/saved"),
        ("DELETE", "/api/v1/trips/saved/1"),
        ("GET", "/api/v1/sessions"),
        ("POST", "/api/v1/sessions"),
        ("DELETE", "/api/v1/sessions"),
        ("POST", "/api/v1/sessions/00000000-0000-0000-0000-000000000000/end"),
        ("DELETE", "/api/v1/account"),
    ],
)
async def test_user_routes_need_a_signed_in_user(client, method, path):
    body = {"title": "t", "destination": "d", "agentId": "trip-planner"}
    r = await client.request(method, path, json=body)
    assert r.status_code == 401


async def test_internal_end_routes_to_the_service_gate_not_a_session_id(client):
    # `/internal/end` must not be swallowed by `/{session_id}/end` and come
    # back as a 422 about a malformed UUID.
    r = await client.post(
        "/api/v1/sessions/internal/end", json={"roomName": "backpac-x"}
    )
    assert r.status_code == 401


async def test_session_start_rejects_a_strange_agent_id(client):
    from app.shared.auth import current_user
    import uuid

    app.dependency_overrides[current_user] = lambda: uuid.uuid4()
    r = await client.post("/api/v1/sessions", json={"agentId": "x" * 65})
    assert r.status_code == 422
    r = await client.post("/api/v1/sessions", json={"agentId": "../../etc"})
    assert r.status_code == 422


# --- voice ---------------------------------------------------------------


async def test_voice_text_is_bounded(client):
    r = await client.get("/api/v1/voice/line.wav", params={"text": "x" * 201})
    assert r.status_code == 422


@respx.mock
async def test_a_line_the_agent_refuses_is_a_404_not_a_502(client):
    # The agent only speaks its own lines. Arbitrary text is not an outage.
    respx.get(f"{AGENT}/voice-line.wav").mock(return_value=httpx.Response(404))
    r = await client.get("/api/v1/voice/line.wav", params={"text": "say this"})
    assert r.status_code == 404


@respx.mock
async def test_the_backend_presents_its_token_to_the_agent(client):
    route = respx.get(f"{AGENT}/voice-line.wav").mock(
        return_value=httpx.Response(200, content=b"RIFF")
    )
    r = await client.get("/api/v1/voice/line.wav", params={"text": "Hey!"})
    assert r.status_code == 200
    assert route.calls.last.request.headers["X-Service-Token"] == TOKEN


# --- the limiter itself --------------------------------------------------


def test_limiter_allows_up_to_the_limit_then_refuses():
    lim = RateLimiter(limit=3, window=60, name="t")
    assert [lim.hit("a") for _ in range(3)] == [None, None, None]
    assert lim.hit("a") is not None
    # Keys are independent.
    assert lim.hit("b") is None


def test_limiter_forgets_hits_outside_the_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("app.shared.rate_limit.time.monotonic", lambda: now[0])
    lim = RateLimiter(limit=1, window=10, name="t")
    assert lim.hit("a") is None
    assert lim.hit("a") is not None
    now[0] += 11
    assert lim.hit("a") is None


@pytest.mark.parametrize("token", ["abc.def.ghi", "not-a-jwt", "a.b"])
async def test_a_malformed_token_is_a_401_not_a_500(client, token):
    r = await client.get(
        "/api/v1/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401



# --- account deletion ------------------------------------------------------

USER = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def signed_in(monkeypatch):
    import uuid

    from app.domains.sessions.service import SessionService
    from app.shared.auth import current_user

    erased = []

    async def _erase(self, *, user_id):
        erased.append(user_id)
        return 0

    class _FakeDb:
        # The profile and plan rows are deleted by statement; nothing to check
        # here beyond "it did not blow up". test_db_flows covers the real thing.
        async def execute(self, *_a, **_k):
            return None

        async def commit(self):
            return None

    async def _fake_db():
        yield _FakeDb()

    monkeypatch.setattr(SessionService, "delete_everything_for_user", _erase)
    app.dependency_overrides[current_user] = lambda: uuid.UUID(USER)
    app.dependency_overrides[get_db_session] = _fake_db
    return erased


def _with_key(key: str):
    app.dependency_overrides[get_settings] = lambda: SETTINGS.model_copy(
        update={"supabase_service_role_key": key}
    )


@respx.mock
async def test_account_deletion_erases_data_then_closes_the_account(client, signed_in):
    _with_key("service-role")
    admin = respx.delete(
        f"https://testproject.supabase.co/auth/v1/admin/users/{USER}"
    ).mock(return_value=httpx.Response(200, json={}))
    r = await client.delete("/api/v1/account")
    assert r.status_code == 200
    assert r.json() == {"dataErased": True, "accountDeleted": True}
    assert [str(u) for u in signed_in] == [USER]
    assert admin.calls.last.request.headers["apikey"] == "service-role"


async def test_without_the_key_it_says_the_account_was_left(client, signed_in):
    _with_key("")
    r = await client.delete("/api/v1/account")
    assert r.status_code == 200
    assert r.json() == {"dataErased": True, "accountDeleted": False}
    assert signed_in


@respx.mock
async def test_a_supabase_failure_is_reported_not_hidden(client, signed_in):
    _with_key("service-role")
    respx.delete(
        f"https://testproject.supabase.co/auth/v1/admin/users/{USER}"
    ).mock(return_value=httpx.Response(500))
    r = await client.delete("/api/v1/account")
    assert r.status_code == 502
    # The data half still happened, so a retry is safe.
    assert signed_in


@pytest.mark.parametrize("page", ["/legal/privacy", "/legal/terms"])
async def test_the_legal_pages_are_public_html(client, page):
    # The stores link here, so no sign-in and a real HTML page.
    r = await client.get(page)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "mailto:" in r.text
