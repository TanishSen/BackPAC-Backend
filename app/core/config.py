"""Application settings, loaded once from the environment.

Nothing anywhere else in the codebase reads `os.environ` directly. Everything
that varies between a laptop and production lives here, so there is exactly one
place to look when something is misconfigured.

Values come from (in order): a real environment variable, then the `.env` file
next to the project, then the default written here. Copy `.env.example` to
`.env` and fill it in — see that file for what each key is.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- identity / ops ----------------------------------------------------
    service_name: str = "backpac-be"
    environment: str = "local"
    log_level: str = "INFO"

    # Comma-separated list of origins allowed to call this API from a browser.
    # The Flutter web build and any local tooling go here. "*" is fine for the
    # hackathon; tighten it before anything real.
    cors_origins: str = "*"

    # --- database ----------------------------------------------------------
    # asyncpg driver. The default points at a local Postgres; override in .env.
    database_url: str = (
        "postgresql+asyncpg://backpac:backpac@localhost:5432/backpac"
    )

    # --- Supabase (identity) -----------------------------------------------
    # The app signs users in with Supabase Auth and sends the resulting JWT as
    # `Authorization: Bearer <token>`. This API verifies it against Supabase's
    # published signing keys (JWKS) — those are public, so no Supabase secret
    # is needed here. The service-role key is deliberately NOT a setting: this
    # API talks to Postgres directly and never needs to impersonate anyone.
    supabase_project_ref: str = ""

    @property
    def supabase_jwks_url(self) -> str:
        return (
            f"https://{self.supabase_project_ref}.supabase.co"
            "/auth/v1/.well-known/jwks.json"
        )

    @property
    def supabase_issuer(self) -> str:
        return f"https://{self.supabase_project_ref}.supabase.co/auth/v1"

    @property
    def auth_configured(self) -> bool:
        return bool(self.supabase_project_ref)

    # --- agent -> API service auth -----------------------------------------
    # The agent writes the transcript as it speaks, but it holds no user token:
    # it is a server, not a signed-in person. It authenticates to the internal
    # message-logging route with this shared secret instead. Must match
    # BACKEND_SERVICE_TOKEN in the agent's environment. Empty means the
    # internal routes are disabled outright rather than left open.
    service_token: str = ""

    # --- flight search (Travelpayouts / Aviasales) -------------------------
    # A free affiliate API. The token authenticates us; the marker is what
    # turns a booking click into commission, so a deployment with a token and
    # no marker works and earns nothing.
    travelpayouts_token: str = ""
    travelpayouts_marker: str = ""

    @property
    def flights_live(self) -> bool:
        """True when real flight search is configured.

        Without it the API serves clearly-labelled sample flights rather than
        failing, so the app and the agent stay usable on a laptop with no
        keys — but nothing pretends the numbers are real.
        """
        return bool(self.travelpayouts_token)

    # --- LiveKit (the voice room the phone and the agent both join) --------
    # LiveKit is a hosted service (livekit.io free tier) or a self-hosted
    # server. This backend only needs the credentials to MINT a room + token;
    # it never streams audio itself.
    livekit_url: str = "wss://your-project.livekit.cloud"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # --- the agent runtime (BackPAC-Agent) ---------------------------------
    # Where the agent service listens. `POST /start` on it puts a bot into the
    # LiveKit room this backend just created.
    agent_base_url: str = "http://localhost:8080"
    agent_timeout_seconds: float = 20.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def livekit_configured(self) -> bool:
        """True only when real LiveKit credentials are set.

        The default URL is a placeholder, so a session-start that would fail
        with a confusing LiveKit error can be caught early with a clear message
        instead. See sessions/service.py.
        """
        return (
            "your-project" not in self.livekit_url
            and bool(self.livekit_api_key)
            and bool(self.livekit_api_secret)
        )


@lru_cache
def get_settings() -> Settings:
    """One cached Settings instance for the whole process."""
    return Settings()
