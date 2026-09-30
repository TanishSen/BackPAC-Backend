# BackPAC Backend — a guide for the developer picking this up

You don't need to know FastAPI deeply to work here. The whole codebase follows
**one rule**, and if you follow it too, everything stays clean:

> A request flows **router → service → repository → database**.
> Each layer only talks to the one below it.

- **router** — reads the request, calls a service, returns the response. No
  logic, no SQL. (`app/domains/*/router.py`)
- **service** — the actual work / decisions. Calls providers, orchestrates
  steps, calls a repository for storage. (`app/domains/*/service.py`)
- **repository** — the *only* place SQL is written. (`app/domains/*/repository.py`)
- **schemas** — the shapes that travel over HTTP (what the app sends/gets).
- **models** — the shapes stored in the database.

Copy the `trips` domain when you build a new one — it shows the full path.

## Run it

```bash
cp .env.example .env         # then fill in the values (see below)
uv venv --python 3.12 .venv  # or: python3.12 -m venv .venv
uv pip install -r requirements.txt
alembic upgrade head         # the schema is Alembic's
uvicorn app.main:app --reload --port 8000
```

In a container, `entrypoint.sh` migrates and then serves on `$PORT`.

Open **http://localhost:8000/docs** — FastAPI generates a live, clickable page
for every endpoint below. That is the fastest way to see and test them.

## What goes in `.env`

| Key | What it's for | Without it |
|---|---|---|
| `ENVIRONMENT` | `production` hides internal details from `/readyz`. | — |
| `DATABASE_URL` | Supabase Postgres (`postgresql+asyncpg://…`). Migrations are Postgres-only. | history + saved trips 503 |
| `SUPABASE_PROJECT_REF` | verifies the app's sign-in tokens against Supabase's public keys. | every signed-in route 503 |
| `SUPABASE_SERVICE_ROLE_KEY` | closes the Supabase sign-in account on `DELETE /account`. Server-only secret. | deletion erases data, leaves the sign-in |
| `LIVEKIT_URL/_API_KEY/_API_SECRET` | mint voice rooms. **Must match `BackPAC-Agent/.env` exactly.** | `POST /sessions` 503 |
| `AGENT_BASE_URL` | where BackPAC-Agent runs. | `POST /sessions` 502 |
| `SERVICE_TOKEN` | shared secret between this API and the agent, both directions. **Must equal the agent's `BACKEND_SERVICE_TOKEN`.** | no transcripts, no search |
| `TRAVELPAYOUTS_TOKEN/_MARKER` | live flight prices + affiliate marker. | labelled sample flights |
| `REVENUECAT_WEBHOOK_AUTH` | the Authorization value RevenueCat's webhook sends. | webhook 503; Premium still syncs via `REVENUECAT_SECRET_KEY` |
| `REVENUECAT_SECRET_KEY` | v1 secret key: ask RevenueCat directly after a purchase. | webhook events applied as sent |
| `PREMIUM_ENTITLEMENT_ID` | the RevenueCat entitlement (default `premium`). | — |
| `FREE_MONTHLY_TRIP_PLANS` | new plans per month on the free plan. **0 = unlimited, no upgrade prompts.** | — |
| `DOCS_ENABLED` | serve `/docs`. | — |
| `SESSION_START_LIMIT` / `_WINDOW_SECONDS` | calls a user may start per window (default 10 / 600s). | — |
| `SEARCH_LIMIT` / `_WINDOW_SECONDS` | searches per signed-in user (default 30 / 60s). The agent is exempt. | — |
| `VOICE_LIMIT` / `_WINDOW_SECONDS` | welcome-line fetches per IP (default 600 / 60s). | — |

**A database that is down does not stop the API.** Voice sessions still need it
(the row is where the transcript goes), but search and the welcome lines do
not. If it was unreachable at boot, the next request that needs it retries the
connection (at most every 10s), so a blip heals without a restart.
`GET /readyz` says which state you are in.

The Anthropic, ElevenLabs and Azure keys are **not** here — they live in the
agent. This service never calls Claude directly.

---

## The endpoints

Base path for everything except health is `/api/v1`. Errors are JSON:
`{"error": "…"}` from the app's own errors, `{"detail": …}` from auth and
validation. `429` carries `Retry-After`.

**Auth column:** *user* = `Authorization: Bearer <Supabase access token>`;
*service* = `X-Service-Token: <SERVICE_TOKEN>` (the agent); *public* = none.

### Health

| Method | Path | Auth | Does |
|---|---|---|---|
| GET | `/healthz` | public | Process alive. For load balancers. |
| GET | `/readyz` | public | What is configured: LiveKit, auth, service token, live flights, database. Touches no upstream. |

### Sessions — voice calls and history

| Method | Path | Auth | Does |
|---|---|---|---|
| POST | `/sessions` | user | Start (or `resumeSessionId` to resume) a call. Mints the room + token, puts the agent in it, returns `{sessionId, agentId, livekit:{url,token,roomName}, isResuming, previousMessages}`. Rate limited per user. |
| GET | `/sessions?limit&offset&status&saved&favourite&groupId&mode` | user | History, newest first. `{sessions:[…], hasMore}`. Filters combine. `status`: `active` (planning), `completed`, `archived`; `mode`: `train\|flight\|stay`. |
| GET | `/sessions/{id}` | user | One conversation with `messages` and `tripResults`. |
| PATCH | `/sessions/{id}` | user | `{title?, status?: active\|completed\|archived, saved?, favourite?, groupId?}`. `groupId: null` takes it out of its group; leaving it out leaves the group alone. |
| POST | `/sessions/{id}/end` | user | Hang up: sets `endedAt` and stops the agent's bot now. Idempotent. |
| DELETE | `/sessions/{id}` | user | Erase one conversation (transcript and cards cascade). 204. |
| DELETE | `/sessions` | user | Erase everything this user has, saved trips included. 204. |
| POST | `/sessions/internal/messages` | service | Agent appends a turn. 202. |
| POST | `/sessions/internal/title` | service | Agent names the conversation. 202. |
| POST | `/sessions/internal/trip-results` | service | Agent records a card it showed. 202. |
| POST | `/sessions/internal/end` | service | Agent reports the call ended. 202. |

`POST /sessions` returns **402** when a free account has used this month's
`FREE_MONTHLY_TRIP_PLANS`. Resuming an existing conversation never counts.

### Groups — the user's folders ("Mountains", "Desert")

| Method | Path | Auth | Does |
|---|---|---|---|
| GET | `/groups` | user | `[{id, name, count, createdAt}]`, oldest first. |
| POST | `/groups` | user | `{name}` (≤40, unique per user, case-insensitive). 201. Max 50. |
| PATCH | `/groups/{id}` | user | `{name}` — rename. |
| DELETE | `/groups/{id}` | user | Its conversations stay, ungrouped. 204. |

### Account and profile

| Method | Path | Auth | Does |
|---|---|---|---|
| GET | `/me` | user | The profile screen in one call: `{profile:{displayName, homeCity, bio, avatar}, stats:{trips, places, saved, favourites, completed, inProgress, bucketList}, plan:{…}}`. Every count is from real rows; "places" is distinct destinations the agent searched. |
| PATCH | `/me` | user | `{displayName?, homeCity?, bio?, avatar?}`. Only what is sent changes; `""` clears. |
| DELETE | `/account` | user | Erase conversations, groups, saved trips, profile and plan, then close the Supabase sign-in account. `{dataErased, accountDeleted}`. Required by both app stores. |

### Premium (RevenueCat)

| Method | Path | Auth | Does |
|---|---|---|---|
| GET | `/billing/plan` | user | `{billingEnabled, premium, premiumUntil, willRenew, productId, freeMonthlyLimit, usedThisMonth, remainingThisMonth}`. |
| POST | `/billing/sync` | user | Ask RevenueCat now (after a purchase or restore) and return the plan. |
| POST | `/billing/revenuecat` | RevenueCat webhook `Authorization` | Keeps our copy current. Always 200 once authenticated. |

### Trips — search

| Method | Path | Auth | Does |
|---|---|---|---|
| POST | `/trips/search/trains` | service or user | **Sample data** today. `{origin, destination, departDate, passengers}` |
| POST | `/trips/search/flights` | service or user | Live Travelpayouts prices (approximate) when configured. |
| POST | `/trips/search/stays` | service or user | **Sample data** today. `{destination, checkIn, checkOut, guests}` |

> These are the contract the **agent** calls too. If you change a field name,
> change `BackPAC-Agent/src/bot/core/tools.py` in the same release.

### Trips — saved

| Method | Path | Auth | Does |
|---|---|---|---|
| POST | `/trips/saved` | user | `{title, destination, nights}`. Owner is the token's user. 201. |
| GET | `/trips/saved` | user | The caller's saved trips, newest first. |
| DELETE | `/trips/saved/{id}` | user | Remove one. 204. |
| GET | `/trips/saved/{user_ref}` | user | Deprecated; only ever returns the caller's own. |

### Voice — what the orb says

Public (the welcome screen plays before sign-in), rate limited per IP. The
agent speaks **only its own fixed lines**: any other `text` is a 404, so this
cannot be used as a free text-to-speech API on our ElevenLabs bill.

| Method | Path | Auth | Does |
|---|---|---|---|
| GET | `/voice/greeting` (optional `?text=`) | public | One opening line: `{text, levels, frameMs}`. |
| GET | `/voice/welcome-lines` | public | `{greeting:[…], poke:[…], idle:[…]}`. |
| GET | `/voice/line.wav?text=…` | public | The audio for one of those lines. Immutable, cacheable. |

---

## What is still sample data

Trains and stays return fixed sample results (`trips/service.py`). Flights are
live when `TRAVELPAYOUTS_TOKEN` is set. Swapping in a provider is a change to
that one file behind the existing schema.

## Two rules that will save you

- **Never write SQL outside a repository.** If a route or service imports
  `select`, something is in the wrong layer.
- **Raise domain errors, not HTTP.** In a service, `raise NotFoundError("...")`
  from `app/shared/exceptions.py`. It becomes the right status code
  automatically. Don't `raise HTTPException` in a service.
