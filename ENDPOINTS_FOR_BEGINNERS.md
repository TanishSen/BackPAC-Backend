# BackPAC Endpoints — the beginner's version

Every endpoint the backend has, in plain words. What it does, where you see it
in the app, and what happens inside.

If you read one thing, read this:

> **Three different callers knock on this backend, and each has its own key.**
>
> | Who knocks | What they send | Which endpoints |
> |---|---|---|
> | **The app** (phone/web) | a user login token (`Authorization: Bearer ...`) | `/sessions*` |
> | **The agent** (the voice robot) | a shared password (`X-Service-Token`) | `/sessions/internal/*` |
> | **Anyone** | nothing | search, voice, saved trips, health |

The app never talks to the agent directly. It only ever talks to this backend.

---

## All 19 endpoints at a glance

| # | Method + Path | In one line | Who calls it |
|---|---|---|---|
| 1 | `GET /healthz` | "Are you alive?" | monitoring |
| 2 | `GET /readyz` | "Are you set up properly?" | you, when debugging |
| 3 | `POST /api/v1/sessions` | Start (or resume) a voice call | app |
| 4 | `GET /api/v1/sessions` | List my past chats | app |
| 5 | `GET /api/v1/sessions/{id}` | One chat, in full | *nobody yet* |
| 6 | `PATCH /api/v1/sessions/{id}` | Rename / archive / bookmark a chat | app |
| 7 | `DELETE /api/v1/sessions/{id}` | Delete one chat | app |
| 8 | `DELETE /api/v1/sessions` | Delete all my chats | *nobody yet* |
| 9 | `POST /api/v1/sessions/internal/messages` | Save one line of the conversation | agent |
| 10 | `POST /api/v1/sessions/internal/title` | Name the conversation | agent |
| 11 | `POST /api/v1/sessions/internal/trip-results` | Save a trip card that was shown | agent |
| 12 | `POST /api/v1/trips/search/trains` | Find trains | agent |
| 13 | `POST /api/v1/trips/search/flights` | Find flights | agent |
| 14 | `POST /api/v1/trips/search/stays` | Find hotels | agent |
| 15 | `POST /api/v1/trips/saved` | Save a trip | *nobody yet* |
| 16 | `GET /api/v1/trips/saved/{user_ref}` | List saved trips | *nobody yet* |
| 17 | `GET /api/v1/voice/greeting` | The spoken "hello" | app |
| 18 | `GET /api/v1/voice/welcome-lines` | All the hello lines at once | app |
| 19 | `GET /api/v1/voice/line.wav` | The actual sound file | app |

> **"nobody yet"** means the endpoint works, but no screen calls it today. It
> is built and waiting.

---

## 1. Health — "is the server okay?"

### `GET /healthz`
- **Does:** replies `{"status":"ok"}`.
- **UI:** none. You never see it.
- **How:** returns a fixed answer instantly. It checks nothing, on purpose — so
  a slow database can't make the server look dead.

### `GET /readyz`
- **Does:** tells you what *is and isn't* configured.
- **UI:** none — this is your debugging tool.
- **How:** reports flags the server already knows, like
  `livekit_configured: false` and `database_ready: true`.
- **Why you care:** when voice calls won't start, open this first. It usually
  answers the question in one look.

---

## 2. Sessions — the heart of the app

A "session" = one conversation with the AI. Everything here needs a **logged-in
user**, and you only ever see **your own** rows.

### `POST /api/v1/sessions` ⭐ the most important one
- **Does:** starts a voice call and hands back a room to join.
- **UI:** you tap the mic / "start talking". This fires.
- **How** (4 steps, in this order):
  1. Creates a row in the database for the conversation.
  2. Asks **LiveKit** for a room + a token (LiveKit carries the live audio).
  3. Tells the **agent** "go join this room".
  4. Only *then* saves the row for good, and returns the room + token.
- **Why that order:** if the agent is down, the call was never going to work, so
  no row is saved. Your history stays clean instead of filling with chats that
  never happened.
- **Resuming:** send `resumeSessionId` and it reuses the *old* room name. The
  agent remembers the earlier conversation, and you get the last 50 messages
  back to paint on screen.

### `GET /api/v1/sessions`
- **Does:** your conversations, newest first.
- **UI:** the **History** screen (and the home screen list).
- **How:** reads your rows from the database, a page at a time.
- **Handy:** `?limit=20&offset=0` for paging, `?saved=true` for only bookmarked
  ones, `?status=archived` for the archive.

### `GET /api/v1/sessions/{id}`
- **Does:** one conversation, complete — every message and every trip card.
- **UI:** *not wired up yet.* Today the app gets the recent messages from
  `POST /sessions` when resuming.
- **How:** one lookup, filtered by your user id so you can't read someone
  else's.

### `PATCH /api/v1/sessions/{id}`
- **Does:** changes one thing about a chat. Four buttons, one endpoint.
- **UI:** the menu on a history item — **Rename**, **Archive**, **Bookmark**.
- **How:** send only the field you want to change:

  | Send this | Result |
  |---|---|
  | `{"title": "Goa trip"}` | renames it |
  | `{"status": "archived"}` | hides it away |
  | `{"status": "active"}` | brings it back |
  | `{"saved": true}` | bookmarks it |

- **Note:** archived and bookmarked are separate. "I'm done with this" and "I
  want to find this again" are different wishes.

### `DELETE /api/v1/sessions/{id}`
- **Does:** erases one chat — the row, its messages, its cards.
- **UI:** **Delete** in the history item menu.
- **How:** a real delete. Gone. Use `PATCH` + archive if you want it back later.
- **Returns:** `204` — success, nothing to send back.

### `DELETE /api/v1/sessions`
- **Does:** erases **everything** you ever said here.
- **UI:** *not wired up yet* — this is the "delete my account" half.
- **How:** the app would delete your login, and this deletes your data. It is
  also the honest answer when someone asks you to erase their data by law.

---

## 3. Internal — the agent writing while you talk

These three are **hidden**. They don't appear on `/docs`, and the app can't use
them. Only the agent can, using a shared password.

They all say **who** by room name — because the room is the only thing the agent
knows about the conversation.

They all return **`202 Accepted`**, which means *"got it, I'll deal with it."*
Not `201 Created`. The difference matters: the agent is mid-phone-call and must
not stand around waiting to hear where the message was filed.

### `POST /api/v1/sessions/internal/messages`
- **Does:** saves one line of the chat — yours or the agent's.
- **UI:** this is *why* your old conversation is still there tomorrow.
- **How:** finds the session by room name, appends one message row.

### `POST /api/v1/sessions/internal/title`
- **Does:** names the conversation.
- **UI:** the title on each History card.
- **How:** the agent waits until it understands the conversation, then sends a
  name like *"Weekend in Goa"*. Better than guessing from the first sentence.

### `POST /api/v1/sessions/internal/trip-results`
- **Does:** saves a trip card that appeared on screen.
- **UI:** the flight/train/hotel cards — so they're still there when you reopen
  the chat.
- **How:** stores the card exactly as it was shown.

---

## 4. Trips — searching, and saving

### `POST /api/v1/trips/search/trains` · `/flights` · `/stays`
- **Does:** finds trains, flights, or places to stay.
- **UI:** you *say* "find me a flight to Goa". You never press a search button —
  the agent decides to call these while you talk.
- **How:** no database at all. The request goes straight out to an outside
  travel provider and comes back.
- **What's real today:**
  - **Flights** — real, if a Travelpayouts key is configured. Sample data if not.
  - **Trains and stays** — sample data. Marked `TODO` in the code, waiting for a
    provider.
- **Nice detail:** if the provider breaks, you get an **empty list**, not an
  error. A failed search should make the agent say "I couldn't find anything",
  not crash your phone call.
- **Send:** `{"origin": "DEL", "destination": "GOI", "date": "2026-10-02"}`

### `POST /api/v1/trips/saved`
- **Does:** saves a trip you liked.
- **UI:** *not wired up yet.*
- **How:** this is the **teaching example** for the codebase — the one endpoint
  that walks the full path `router → service → repository → database`. Copy it
  when you build something new.
- **Returns:** `201 Created`.

### `GET /api/v1/trips/saved/{user_ref}`
- **Does:** lists someone's saved trips.
- **UI:** *not wired up yet.*
- **How:** reads them back out of the database.
- **⚠️ Careful:** the user is in the **URL**, not a login token. So anyone who
  guesses a `user_ref` can read those trips. Fine for a demo; must move to a
  real login before this ships.

---

## 5. Voice — the talking welcome screen

### `GET /api/v1/voice/greeting`
- **Does:** gets the spoken "hello" for the welcome screen.
- **UI:** the glowing orb on first open — it speaks, and pulses in time.
- **How:** the backend asks the **agent** to make the audio (the ElevenLabs key
  lives there, not here). Returns the text plus a "loudness track" the orb uses
  to pulse.
- **If the agent is down:** the app just stays quiet. A hello is nice, not
  essential.

### `GET /api/v1/voice/welcome-lines`
- **Does:** every line the orb can say, in one go.
- **UI:** tap the orb, it answers **instantly**.
- **How:** the app fetches this once at startup and keeps it. No waiting later.

### `GET /api/v1/voice/line.wav`
- **Does:** the actual sound file for one line.
- **UI:** the audio you hear.
- **How:** returns a real `.wav` file, not text-encoded audio inside JSON — so
  the browser and the phone can cache it normally.
- **Cached forever:** the same words always make the same sound, so it's marked
  `immutable` and downloaded only once, ever.

---

## Quick reference: what the status codes mean here

| Code | Meaning in this backend |
|---|---|
| `200` | Here's your answer. |
| `201` | Made a new thing (saved trip). |
| `202` | Got it, I'll handle it — the agent doesn't wait. |
| `204` | Done. Nothing to send back (deletes). |
| `401` | You're not logged in, or your login expired. |
| `404` | Not found — *or* it isn't yours. Deliberately the same answer, so nobody can probe for which ids exist. |
| `503` | Something I depend on isn't ready (no database, or LiveKit not configured). Check `/readyz`. |

---

## Try it yourself

The backend generates a live, clickable page for every public endpoint:

**http://localhost:8000/docs**

You can fire real requests from that page. The hidden `/internal/*` three won't
be listed there — that's intentional.

Health checks need no setup at all:

```bash
curl http://localhost:8000/healthz     # {"status":"ok"}
curl http://localhost:8000/readyz      # what's configured
```
