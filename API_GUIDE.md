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
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** — FastAPI generates a live, clickable page
for every endpoint below. That is the fastest way to see and test them.

## What goes in `.env`

| Key | What it's for | Needed for |
|---|---|---|
| `DATABASE_URL` | the database. Defaults to SQLite, so it runs with no infrastructure; point it at Postgres for anything shared. | saved-trips endpoints |
| `LIVEKIT_URL/_API_KEY/_API_SECRET` | mint voice rooms (free tier at livekit.io). **Must match `BackPAC-Agent/.env` exactly.** | `POST /sessions` |
| `AGENT_BASE_URL` | where BackPAC-Agent runs | `POST /sessions` |

**A missing database does not stop the API.** Starting a voice call and
searching for trips touch no tables at all, so if the database is unreachable
the service logs a warning and carries on serving them; only the saved-trip
routes return 503. `GET /readyz` tells you which state you are in. That
deliberate choice is in `db/session.py` — a demo should not die because nobody
started Postgres.

The Anthropic and ElevenLabs keys are **not** here — those live in the *agent*,
not the backend. This service never calls Claude directly.

---

## The endpoints

Base path for everything except health is `/api/v1`.

### Health

| Method | Path | Does |
|---|---|---|
| GET | `/healthz` | "Is the process alive?" Returns `{"status":"ok"}`. For load balancers. |
| GET | `/readyz` | "Is it configured?" Reports `livekit_configured` and `database_ready` — check this first when anything won't start. It touches no upstream, because a health check that fans out is a health check that flaps. |

### Sessions — starting a voice call

**`POST /api/v1/sessions`**
The one multi-step endpoint. It mints a LiveKit room, mints an access token
scoped to that room, tells the agent to join the room, and returns the token to
the app. By the time the app gets the response, the bot is already in the room.

- **Send:** `{ "agentId": "trip-planner", "participantName": "Jasmin" }`
- **Get:** `{ "sessionId", "agentId", "livekit": { "url", "token", "roomName" } }`
- **Flutter then:** joins `livekit.url` with `livekit.token` using `livekit_client`.
- **Already done** — no work needed unless you add session history (see below).

### Trips — search (called by the agent's tools *and* the app)

These three share the same idea: take a search, return options. **Right now they
return mock data.** Your main job is to replace the mock body in
`app/domains/trips/service.py` with a real provider call — keep the return
shape identical and nothing else has to change.

| Method | Path | Does | Your job |
|---|---|---|---|
| POST | `/api/v1/trips/search/trains` | Trains between two cities on a date. | Wire to IRCTC / RapidAPI. Replace `TripService.search_trains`. |
| POST | `/api/v1/trips/search/flights` | Flights between two cities on a date. | Wire to a flight API. Replace `search_flights`. |
| POST | `/api/v1/trips/search/stays` | Hotels in a city between two dates. | Wire to a hotel API. Replace `search_stays`. |

- **Send (trains/flights):** `{ "origin", "destination", "departDate": "2026-01-20", "passengers": 2 }`
- **Send (stays):** `{ "destination", "checkIn", "checkOut", "guests": 2 }`
- **Get:** a list of options (see `schemas.py` for exact fields).

> These are the contract the **agent** calls too (as `search_trains` etc.). If
> you change a field name, tell whoever owns the agent — their tool in
> `BackPAC-Agent/src/bot/core/tools.py` sends this exact shape.

### Voice — what the orb says

Three routes, all proxies: the agent synthesises, because that is where the
ElevenLabs key lives, and these exist so the app still only ever talks to one
host. **None of them is a call** — no LiveKit room, no session, no microphone
permission. The welcome screen should not need any of that to say hello.

| Method | Path | Does |
|---|---|---|
| GET | `/api/v1/voice/greeting` (optional `?text=`) | One opening line. |
| GET | `/api/v1/voice/welcome-lines` | Every line the orb might say: the opener, what it says when poked, what it says when you leave it alone. |
| GET | `/api/v1/voice/line.wav?text=…` | The audio for one line. |

The JSON carries `{ "text", "levels": [0..1], "frameMs": 50 }` — `levels` is one
loudness value per frame, so the app drives the orb's mouth from the actual
waveform rather than a generic wobble. A few hundred bytes, so it travels
inline.

**The audio does not travel inline.** Base64 in the JSON made
`welcome-lines` a 2.5 MB body that no cache could reuse; as separate files it
is 5.7 KB of JSON plus ~65 KB per line, fetched once and then served from the
browser's cache (the responses are `immutable` — the text fully determines the
audio).

Every line is cached agent-side, so a cold `welcome-lines` costs one synthesis
per line (~5s for all nineteen, done in parallel) and every request after that
is ~20ms. The app fetches it in the background, so neither case is on screen.

### Trips — saved (the database example)

These two exist to show the full router→service→repository→DB path end to end.
Read them when you build any DB-backed feature.

| Method | Path | Does |
|---|---|---|
| POST | `/api/v1/trips/saved` | Save a trip for a user. Writes a row. |
| GET | `/api/v1/trips/saved/{user_ref}` | List that user's saved trips, newest first. |

---

## The three jobs waiting for you, in order

1. **Real search providers.** Replace the three mock methods in
   `trips/service.py`. This is the actual product — an agent that can't really
   search is a demo. Everything else already works around it.
2. **Migrations.** Tables are created at startup with
   `Base.metadata.create_all` (`db/session.py`). That is fine while the schema
   is disposable, but it cannot *change* a table that already exists. Move to
   Alembic before this database holds anything anyone would miss.
3. **Auth.** Every endpoint is open right now. When you add login, sessions and
   saved-trips should require it. `user_ref` becomes the logged-in user.

## Two rules that will save you

- **Never write SQL outside a repository.** If a route or service imports
  `select`, something is in the wrong layer.
- **Raise domain errors, not HTTP.** In a service, `raise NotFoundError("...")`
  from `app/shared/exceptions.py`. It becomes the right status code
  automatically. Don't `raise HTTPException` in a service.
