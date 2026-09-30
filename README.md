# BackPAC-Backend

# BackPAC-BE

The BackPAC backend. FastAPI, layered `router → service → repository → database`.
Two jobs: **start voice sessions** (mint a LiveKit room + token, put the agent
in it) and **serve the trip domain** (search trains/flights/stays, saved trips).

- New here? Read **API_GUIDE.md** — every endpoint, what it does, what's left to
  build, written for someone new to the codebase.
- Run: `cp .env.example .env` → fill it → `uv pip install -r requirements.txt`
  → `uvicorn app.main:app --reload --port 8000` → open `/docs`.

This service never calls Claude or ElevenLabs — those keys live in BackPAC-Agent.

---

Part of **backPAC**, a voice travel planner — start at
[BackPAC-Fe](https://github.com/TanishSen/BackPAC-Fe) for the overview and how the
three repositories fit together. MIT licensed; see [LICENSE](LICENSE).
