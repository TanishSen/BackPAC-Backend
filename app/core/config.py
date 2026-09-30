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
    # The Flutter web build and any local tooling go here. The phone app is not
    # a browser and ignores CORS entirely, so this only matters for web builds.
    cors_origins: str = "*"

    # Serve /docs and /openapi.json. Handy for the team; set false to keep the
    # route map private on a public deployment.
    docs_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    # --- abuse limits --------------------------------------------------------
    # Every call these guard spends money or a shared quota. Per user where
    # there is one, per client IP where there is not. See shared/rate_limit.py.
    #
    # Starting a session puts a bot in a room: Claude, ElevenLabs and Azure
    # all bill for it. Ten in ten minutes is far past anyone hanging up and
    # redialling, and well short of a script.
    session_start_limit: int = 10
    session_start_window_seconds: int = 600
    # Trip search, per signed-in user. The agent is not limited here: it is us,
    # searching for every call in progress at once, and each of those calls is
    # already bounded by the session-start limit above.
    search_limit: int = 30
    search_window_seconds: int = 60
    # The welcome screen's spoken lines, per IP. Generous, because mobile
    # carriers put whole neighbourhoods behind one address; what actually stops
    # abuse is the agent refusing any line that is not its own.
    voice_limit: int = 600
    voice_window_seconds: int = 60

    # --- database ----------------------------------------------------------
    # asyncpg driver. The default points at a local Postgres; override in .env.
    database_url: str = (
        "postgresql+asyncpg://backpac:backpac@localhost:5432/backpac"
    )

    # --- Supabase (identity) -----------------------------------------------
    # The app signs users in with Supabase Auth and sends the resulting JWT as
    # `Authorization: Bearer <token>`. This API verifies it against Supabase's
    # published signing keys (JWKS) — those are public, so no Supabase secret
    # is needed to verify sign-in. This API talks to Postgres directly and
    # never impersonates anyone; the service-role key below exists only to
    # delete an account on its owner's request.
    supabase_project_ref: str = ""

    #: Optional, and used for exactly one thing: deleting a person's sign-in
    #: account when they ask to delete their account (DELETE /api/v1/account).
    #: Only a server holding this key can remove a Supabase auth user, and both
    #: app stores require that in-app deletion actually close the account. It
    #: bypasses row-level security, so it lives only in this server's
    #: environment — never in the app. Unset, account deletion erases our data
    #: and reports that the sign-in account itself was left in place.
    supabase_service_role_key: str = ""

    @property
    def supabase_url(self) -> str:
        return f"https://{self.supabase_project_ref}.supabase.co"

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

    # --- agent <-> API service auth -----------------------------------------
    # The agent writes the transcript as it speaks, but it holds no user token:
    # it is a server, not a signed-in person. It authenticates to the internal
    # routes and to trip search with this shared secret instead, and this API
    # presents the same secret back when it calls the agent. Must match
    # BACKEND_SERVICE_TOKEN in the agent's environment. Empty means the
    # internal routes are disabled outright rather than left open.
    service_token: str = ""

    # --- Premium (RevenueCat) ------------------------------------------------
    # RevenueCat runs the purchase through the App Store / Google Play and is
    # the source of truth for who has Premium; this API keeps a copy (the
    # `entitlements` table) so starting a call can check the plan without a
    # round trip. Two ways that copy is kept fresh, both optional:
    #
    # - the webhook (RevenueCat dashboard -> Integrations -> Webhooks), which
    #   sends REVENUECAT_WEBHOOK_AUTH as its Authorization header, and
    # - REVENUECAT_SECRET_KEY (a v1 *secret* API key), used to ask RevenueCat
    #   directly after a purchase and whenever a webhook arrives.
    revenuecat_webhook_auth: str = ""
    revenuecat_secret_key: str = ""
    #: The entitlement identifier configured in RevenueCat.
    premium_entitlement_id: str = "premium"
    #: New trip plans a free account may start per calendar month (UTC).
    #: 0 turns the allowance off — everyone is unlimited and the app shows no
    #: upgrade prompt, because Premium would then buy nothing. Turn it on only
    #: once RevenueCat and the store products are live, or free users hit a
    #: wall with no way over it.
    free_monthly_trip_plans: int = 0

    @property
    def billing_enabled(self) -> bool:
        """Whether Premium is for sale: there is a limit for it to lift."""
        return self.free_monthly_trip_plans > 0

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
