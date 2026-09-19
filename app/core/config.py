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
