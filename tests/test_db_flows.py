"""The database-backed flows, end to end, against a real Postgres.

Skipped unless TEST_DATABASE_URL points at a migrated, disposable database —
the schema is Postgres-only (JSONB, RLS), so SQLite cannot stand in:

    TEST_DATABASE_URL=postgresql+asyncpg://postgres@localhost/backpac_test \\
        pytest tests/test_db_flows.py

Every table is truncated before each test. Never point this at a database
anyone cares about.

The agent and LiveKit are the only things faked: the agent over HTTP (respx),
LiveKit by minting tokens with throwaway credentials, which needs no network.
"""

import json
import os
import uuid

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.domains.sessions import router as sessions_router_module
from app.main import app
from app.shared.auth import current_user

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

TOKEN = "test-service-token"
AGENT = "http://agent.test"
SETTINGS = Settings(
    _env_file=None,
    service_token=TOKEN,
    supabase_project_ref="testproject",
    agent_base_url=AGENT,
    livekit_url="wss://test.livekit.cloud",
    livekit_api_key="key",
    livekit_api_secret="secret-secret-secret-secret-secret",
)
SVC = {"X-Service-Token": TOKEN}

ALICE = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
BOB = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


@pytest.fixture
async def env(monkeypatch):
    engine = create_async_engine(
        DB_URL, poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE sessions, messages, trip_results, saved_trips, "
                "session_groups, profiles, entitlements CASCADE"
            )
        )

    async def _db():
        async with factory() as s:
            yield s

    # `who["settings"]` may be swapped by a test, e.g. to switch the free
    # allowance on; both ways settings are read follow it.
    who = {"user": ALICE, "settings": SETTINGS}
    app.dependency_overrides[get_settings] = lambda: who["settings"]
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[current_user] = lambda: who["user"]
    # The sessions router builds its service from get_settings() directly.
    monkeypatch.setattr(
        sessions_router_module, "get_settings", lambda: who["settings"]
    )
    sessions_router_module.session_start_limiter.reset()

    with respx.mock(assert_all_called=False) as agent:
        agent.post(f"{AGENT}/start").mock(
            return_value=httpx.Response(200, json={"status": "running"})
        )
        agent.post(f"{AGENT}/stop").mock(return_value=httpx.Response(200, json={}))
        async with httpx.AsyncClient() as agent_http:
            app.state.http_client = agent_http
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                yield c, who, agent, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _row(factory, sql: str, **params):
    async with factory() as s:
        return (await s.execute(text(sql), params)).mappings().first()


async def test_a_call_from_start_to_hang_up(env):
    c, who, agent, factory = env

    r = await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})
    assert r.status_code == 200, r.text
    s = r.json()
    sid, room = s["sessionId"], s["livekit"]["roomName"]
    assert s["livekit"]["token"] and not s["isResuming"]

    start = agent.routes[0].calls.last.request
    assert start.headers["X-Service-Token"] == TOKEN
    run_id = json.loads(start.content)["sessionId"]
    row = await _row(factory, "SELECT metadata, ended_at FROM sessions WHERE id=:i", i=sid)
    assert row["metadata"]["agent_run_id"] == run_id
    assert row["ended_at"] is None

    # The agent writes the conversation as it happens.
    for body in (
        {"roomName": room, "role": "user", "content": "Trains to Jaipur on Friday"},
        {"roomName": room, "role": "agent", "content": "Two options."},
    ):
        assert (await c.post("/api/v1/sessions/internal/messages", json=body, headers=SVC)).status_code == 202
    r = await c.post(
        "/api/v1/sessions/internal/trip-results",
        json={"roomName": room, "resultType": "train", "payload": {"rows": [1]}},
        headers=SVC,
    )
    assert r.status_code == 202

    page = (await c.get("/api/v1/sessions")).json()
    assert [x["id"] for x in page["sessions"]] == [sid]
    tile = page["sessions"][0]
    assert tile["title"] == "Trains to Jaipur on Friday"
    assert tile["mode"] == "train" and tile["messageCount"] == 2

    # Hanging up ends it and stops exactly this bot.
    r = await c.post(f"/api/v1/sessions/{sid}/end")
    assert r.status_code == 200 and r.json()["endedAt"]
    stop = agent.routes[1].calls.last.request
    assert json.loads(stop.content) == {"sessionId": run_id}
    assert stop.headers["X-Service-Token"] == TOKEN
    # Idempotent.
    assert (await c.post(f"/api/v1/sessions/{sid}/end")).status_code == 200

    detail = (await c.get(f"/api/v1/sessions/{sid}")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "agent"]
    assert detail["tripResults"][0]["resultType"] == "train"


async def test_resume_reopens_and_ignores_the_replaced_bots_hang_up(env):
    c, who, agent, factory = env
    s = (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).json()
    sid, room = s["sessionId"], s["livekit"]["roomName"]
    old_run = json.loads(agent.routes[0].calls.last.request.content)["sessionId"]
    await c.post(
        "/api/v1/sessions/internal/messages",
        json={"roomName": room, "role": "user", "content": "hello"},
        headers=SVC,
    )
    await c.post(f"/api/v1/sessions/{sid}/end")

    r = await c.post(
        "/api/v1/sessions", json={"agentId": "trip-planner", "resumeSessionId": sid}
    )
    assert r.status_code == 200
    assert r.json()["isResuming"] and len(r.json()["previousMessages"]) == 1
    assert r.json()["livekit"]["roomName"] == room
    new_run = json.loads(agent.routes[0].calls.last.request.content)["sessionId"]
    assert new_run != old_run
    row = await _row(factory, "SELECT metadata, ended_at FROM sessions WHERE id=:i", i=sid)
    assert row["ended_at"] is None and row["metadata"]["agent_run_id"] == new_run

    # The replaced bot reporting its own end must not end the live call.
    await c.post("/api/v1/sessions/internal/end", json={"roomName": room, "runId": old_run}, headers=SVC)
    row = await _row(factory, "SELECT ended_at FROM sessions WHERE id=:i", i=sid)
    assert row["ended_at"] is None
    await c.post("/api/v1/sessions/internal/end", json={"roomName": room, "runId": new_run}, headers=SVC)
    row = await _row(factory, "SELECT ended_at FROM sessions WHERE id=:i", i=sid)
    assert row["ended_at"] is not None


async def test_nobody_sees_or_touches_anyone_elses(env):
    c, who, agent, factory = env
    s = (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).json()
    await c.post(
        "/api/v1/sessions/internal/messages",
        json={"roomName": s["livekit"]["roomName"], "role": "user", "content": "mine"},
        headers=SVC,
    )
    trip = (await c.post("/api/v1/trips/saved", json={"title": "Goa", "destination": "Goa", "userRef": str(BOB)})).json()

    who["user"] = BOB
    sid = s["sessionId"]
    assert (await c.get("/api/v1/sessions")).json()["sessions"] == []
    assert (await c.get(f"/api/v1/sessions/{sid}")).status_code == 404
    assert (await c.patch(f"/api/v1/sessions/{sid}", json={"title": "x"})).status_code == 404
    assert (await c.post(f"/api/v1/sessions/{sid}/end")).status_code == 404
    assert (await c.delete(f"/api/v1/sessions/{sid}")).status_code == 404
    r = await c.post("/api/v1/sessions", json={"agentId": "trip-planner", "resumeSessionId": sid})
    assert r.status_code == 404
    # The body's userRef named Bob, but Alice's token saved it: it is Alice's.
    assert (await c.get("/api/v1/trips/saved")).json() == []
    assert (await c.get(f"/api/v1/trips/saved/{ALICE}")).json() == []
    assert (await c.delete(f"/api/v1/trips/saved/{trip['id']}")).status_code == 404

    who["user"] = ALICE
    assert [t["id"] for t in (await c.get("/api/v1/trips/saved")).json()] == [trip["id"]]
    assert (await c.delete(f"/api/v1/trips/saved/{trip['id']}")).status_code == 204
    assert (await c.get("/api/v1/trips/saved")).json() == []


async def test_starting_calls_is_rate_limited_per_user(env):
    c, who, agent, factory = env
    limit = SETTINGS.session_start_limit
    for _ in range(limit):
        assert (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).status_code == 200
    r = await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    who["user"] = BOB  # someone else is unaffected
    assert (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).status_code == 200


async def test_an_agent_failure_leaves_no_ghost_conversation(env):
    c, who, agent, factory = env
    agent.routes[0].mock(return_value=httpx.Response(500))
    r = await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})
    assert r.status_code == 502
    row = await _row(factory, "SELECT count(*) AS n FROM sessions")
    assert row["n"] == 0


async def test_deleting_the_account_erases_everything_it_owns(env):
    c, who, agent, factory = env
    s = (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).json()
    await c.post(
        "/api/v1/sessions/internal/messages",
        json={"roomName": s["livekit"]["roomName"], "role": "user", "content": "x"},
        headers=SVC,
    )
    await c.post("/api/v1/trips/saved", json={"title": "Goa", "destination": "Goa"})
    who["user"] = BOB
    await c.post("/api/v1/trips/saved", json={"title": "Bob's", "destination": "Leh"})

    who["user"] = ALICE
    r = await c.delete("/api/v1/account")
    assert r.status_code == 200
    assert r.json() == {"dataErased": True, "accountDeleted": False}  # no service key here
    for table, col, owner in (
        ("sessions", "user_id", ALICE),
        ("saved_trips", "user_ref", str(ALICE)),
    ):
        row = await _row(factory, f"SELECT count(*) AS n FROM {table} WHERE {col}=:o", o=owner)
        assert row["n"] == 0
    assert (await _row(factory, "SELECT count(*) AS n FROM messages"))["n"] == 0
    # Bob's things are untouched.
    row = await _row(factory, "SELECT count(*) AS n FROM saved_trips WHERE user_ref=:o", o=str(BOB))
    assert row["n"] == 1



# --- the history page: favourites, groups, status ------------------------


async def _room_of(factory, session_id: str) -> str:
    return (await _row(factory, "SELECT room_name FROM sessions WHERE id=:i", i=session_id))["room_name"]


async def _talked(c, text_="hello") -> dict:
    """A conversation someone actually spoke in — the only kind that lists."""
    s = (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).json()
    await c.post(
        "/api/v1/sessions/internal/messages",
        json={"roomName": s["livekit"]["roomName"], "role": "user", "content": text_},
        headers=SVC,
    )
    return s


async def test_favourites_groups_and_status_narrow_the_list(env):
    c, who, agent, factory = env
    goa = (await _talked(c, "Resorts in Goa"))["sessionId"]
    manali = (await _talked(c, "Paragliding in Manali"))["sessionId"]

    r = await c.patch(f"/api/v1/sessions/{goa}", json={"favourite": True})
    assert r.status_code == 200 and r.json()["favourite"] is True

    g = await c.post("/api/v1/groups", json={"name": "  Mountains "})
    assert g.status_code == 201 and g.json()["name"] == "Mountains"
    gid = g.json()["id"]
    dup = await c.post("/api/v1/groups", json={"name": "mountains"})
    assert dup.status_code == 400

    r = await c.patch(f"/api/v1/sessions/{manali}", json={"groupId": gid, "status": "completed"})
    assert r.status_code == 200 and r.json()["groupId"] == gid
    assert r.json()["status"] == "completed"

    def ids(page):
        return [x["id"] for x in page.json()["sessions"]]

    assert ids(await c.get("/api/v1/sessions", params={"favourite": "true"})) == [goa]
    assert ids(await c.get("/api/v1/sessions", params={"groupId": gid})) == [manali]
    assert ids(await c.get("/api/v1/sessions", params={"status": "completed"})) == [manali]
    await c.post(
        "/api/v1/sessions/internal/trip-results",
        json={"roomName": await _room_of(factory, goa), "resultType": "stay", "payload": {}},
        headers=SVC,
    )
    assert ids(await c.get("/api/v1/sessions", params={"mode": "stay"})) == [goa]
    groups = (await c.get("/api/v1/groups")).json()
    assert [(x["name"], x["count"]) for x in groups] == [("Mountains", 1)]

    # Renaming keeps the conversations in it.
    r = await c.patch(f"/api/v1/groups/{gid}", json={"name": "Hills"})
    assert r.status_code == 200 and r.json() == {**r.json(), "name": "Hills", "count": 1}

    # Leaving groupId out leaves the folder alone; null takes it out.
    await c.patch(f"/api/v1/sessions/{manali}", json={"title": "Manali"})
    assert (await c.get(f"/api/v1/sessions/{manali}")).json()["groupId"] == gid
    await c.patch(f"/api/v1/sessions/{manali}", json={"groupId": None})
    assert (await c.get(f"/api/v1/sessions/{manali}")).json()["groupId"] is None

    # Someone else's group is no group at all.
    who["user"] = BOB
    bobs = (await c.post("/api/v1/groups", json={"name": "Bob's"})).json()["id"]
    assert (await c.patch(f"/api/v1/groups/{gid}", json={"name": "x"})).status_code == 404
    assert (await c.delete(f"/api/v1/groups/{gid}")).status_code == 404
    who["user"] = ALICE
    r = await c.patch(f"/api/v1/sessions/{manali}", json={"groupId": bobs})
    assert r.status_code == 404

    # Deleting a group keeps what was in it.
    await c.patch(f"/api/v1/sessions/{manali}", json={"groupId": gid})
    assert (await c.delete(f"/api/v1/groups/{gid}")).status_code == 204
    detail = (await c.get(f"/api/v1/sessions/{manali}")).json()
    assert detail["groupId"] is None and detail["title"] == "Manali"


# --- the profile page ----------------------------------------------------


async def test_profile_edits_and_journey_numbers(env):
    c, who, agent, factory = env

    me = (await c.get("/api/v1/me")).json()
    assert me["profile"] == {"displayName": None, "homeCity": None, "bio": None, "avatar": None}
    assert me["stats"]["trips"] == 0

    r = await c.patch(
        "/api/v1/me",
        json={"displayName": " Jasmine ", "homeCity": "Kolkata, India", "bio": "Collecting moments", "avatar": "🐱"},
    )
    assert r.status_code == 200
    assert r.json()["profile"] == {
        "displayName": "Jasmine", "homeCity": "Kolkata, India",
        "bio": "Collecting moments", "avatar": "🐱",
    }
    # Only what is sent changes; "" clears.
    r = await c.patch("/api/v1/me", json={"bio": ""})
    assert r.json()["profile"]["bio"] is None
    assert r.json()["profile"]["displayName"] == "Jasmine"
    assert (await c.patch("/api/v1/me", json={"bio": "x" * 161})).status_code == 422

    a = await _talked(c, "Trains to Jaipur")
    b = await _talked(c, "Goa")
    await _talked(c, "Nothing")
    for room, dest in (
        (a["livekit"]["roomName"], "Jaipur"),
        (a["livekit"]["roomName"], " jaipur "),
        (b["livekit"]["roomName"], "Goa"),
    ):
        await c.post(
            "/api/v1/sessions/internal/trip-results",
            json={
                "roomName": room,
                "resultType": "train",
                "payload": {"tool": "search_trains", "query": {"destination": dest}, "result": []},
            },
            headers=SVC,
        )
    # A card from before queries were recorded is not a place.
    await c.post(
        "/api/v1/sessions/internal/trip-results",
        json={"roomName": b["livekit"]["roomName"], "resultType": "stay", "payload": {"result": []}},
        headers=SVC,
    )
    await c.patch(f"/api/v1/sessions/{a['sessionId']}", json={"saved": True, "favourite": True})
    await c.patch(f"/api/v1/sessions/{b['sessionId']}", json={"status": "completed"})
    await c.post("/api/v1/trips/saved", json={"title": "Leh", "destination": "Leh"})
    # A conversation nobody spoke in counts for nothing.
    await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})

    stats = (await c.get("/api/v1/me")).json()["stats"]
    assert stats == {
        "trips": 3, "places": 2, "saved": 1, "favourites": 1,
        "completed": 1, "inProgress": 2, "bucketList": 1,
    }


# --- Premium ---------------------------------------------------------------


async def test_the_free_allowance_only_counts_new_plans(env):
    c, who, agent, factory = env
    who["settings"] = SETTINGS.model_copy(update={"free_monthly_trip_plans": 2})

    plan = (await c.get("/api/v1/billing/plan")).json()
    assert plan["billingEnabled"] and plan["remainingThisMonth"] == 2

    first = await _talked(c)
    await _talked(c)
    # Opened and abandoned: not a plan, not counted.
    await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})
    plan = (await c.get("/api/v1/billing/plan")).json()
    assert plan["usedThisMonth"] == 2 and plan["remainingThisMonth"] == 0

    r = await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})
    assert r.status_code == 402 and "Upgrade" in r.json()["error"]
    # Carrying on an earlier plan always works.
    r = await c.post(
        "/api/v1/sessions",
        json={"agentId": "trip-planner", "resumeSessionId": first["sessionId"]},
    )
    assert r.status_code == 200


async def test_the_webhook_keeps_premium_in_step(env):
    import time

    c, who, agent, factory = env
    who["settings"] = SETTINGS.model_copy(
        update={"free_monthly_trip_plans": 1, "revenuecat_webhook_auth": "whsec"}
    )
    hook = "/api/v1/billing/revenuecat"

    def event(kind, expires_ms, user=ALICE):
        return {"event": {
            "type": kind, "app_user_id": str(user), "entitlement_ids": ["premium"],
            "product_id": "backpac_monthly", "expiration_at_ms": expires_ms,
        }}

    assert (await c.post(hook, json=event("INITIAL_PURCHASE", 1))).status_code == 401
    ok = {"Authorization": "whsec"}
    assert (await c.post(hook, json={"event": {"type": "TEST"}}, headers=ok)).status_code == 200
    anon = {"event": {"type": "INITIAL_PURCHASE", "app_user_id": "$RCAnonymousID:abc"}}
    assert (await c.post(hook, json=anon, headers=ok)).status_code == 200

    await _talked(c)
    assert (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).status_code == 402

    month = int((time.time() + 30 * 86400) * 1000)
    r = await c.post(hook, json=event("INITIAL_PURCHASE", month), headers=ok)
    assert r.status_code == 200
    plan = (await c.get("/api/v1/billing/plan")).json()
    assert plan["premium"] and plan["willRenew"] and plan["remainingThisMonth"] is None
    assert (await c.post("/api/v1/sessions", json={"agentId": "trip-planner"})).status_code == 200

    # Cancelled: still Premium until the date, but it will not renew.
    await c.post(hook, json=event("CANCELLATION", month), headers=ok)
    plan = (await c.get("/api/v1/billing/plan")).json()
    assert plan["premium"] and not plan["willRenew"]

    past = int((time.time() - 60) * 1000)
    await c.post(hook, json=event("EXPIRATION", past), headers=ok)
    plan = (await c.get("/api/v1/billing/plan")).json()
    assert not plan["premium"] and plan["remainingThisMonth"] == 0


async def test_sync_asks_revenuecat_when_it_has_a_key(env):
    c, who, agent, factory = env
    who["settings"] = SETTINGS.model_copy(
        update={"free_monthly_trip_plans": 1, "revenuecat_secret_key": "sk_test"}
    )
    route = agent.get(f"https://api.revenuecat.com/v1/subscribers/{ALICE}").mock(
        return_value=httpx.Response(200, json={"subscriber": {
            "entitlements": {"premium": {
                "expires_date": "2099-01-01T00:00:00Z",
                "product_identifier": "backpac_annual",
            }},
            "subscriptions": {"backpac_annual": {"unsubscribe_detected_at": None}},
        }})
    )
    plan = (await c.post("/api/v1/billing/sync")).json()
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk_test"
    assert plan["premium"] and plan["productId"] == "backpac_annual" and plan["willRenew"]

    # RevenueCat says it lapsed: our copy follows.
    route.mock(return_value=httpx.Response(200, json={"subscriber": {"entitlements": {}}}))
    plan = (await c.post("/api/v1/billing/sync")).json()
    assert not plan["premium"]


async def test_account_deletion_takes_the_profile_groups_and_plan_too(env):
    c, who, agent, factory = env
    await c.patch("/api/v1/me", json={"displayName": "Jasmine"})
    await c.post("/api/v1/groups", json={"name": "Mountains"})
    async with factory() as s:
        await s.execute(
            text("INSERT INTO entitlements (user_id, premium_until) VALUES (:u, now())"),
            {"u": ALICE},
        )
        await s.commit()

    assert (await c.delete("/api/v1/account")).status_code == 200
    for table in ("profiles", "session_groups", "entitlements"):
        n = (await _row(factory, f"SELECT count(*) AS n FROM {table} WHERE user_id=:u", u=ALICE))["n"]
        assert n == 0, table
