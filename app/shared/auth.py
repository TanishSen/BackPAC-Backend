"""Who is calling — Supabase JWT verification, and the two identities we accept.

Two kinds of caller reach this API, and they authenticate differently because
they *are* different:

1. **A person**, from the Flutter app, holding a Supabase Auth JWT. Verified
   here against Supabase's published signing keys. `current_user` turns that
   token into a user id, and every route that touches user data depends on it.

2. **The agent**, which is a server. It has no user to be: it writes the
   transcript while the call is happening, long after the request that started
   it returned. It presents a shared secret instead — see `require_service`.

**Why JWKS rather than the JWT secret.** Supabase now signs with an asymmetric
key (ECC P-256) and publishes the public half at a well-known URL. Verifying
against that means this service holds no Supabase secret at all: the worst a
leak of our config can do is let someone *verify* tokens, not mint them. It
also means key rotation is Supabase's problem — a new `kid` appears, PyJWKClient
fetches it, and nothing here changes.

The fetched keys are cached, so this is one HTTP call on the first request
after boot and none afterwards. A `kid` we have not seen forces a refetch,
which is exactly what a rotation looks like.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# auto_error=False so a missing header reaches our own handler and returns a
# message that says what to do, rather than a bare 403 from Starlette.
_bearer = HTTPBearer(auto_error=False)

_jwks_client: PyJWKClient | None = None


def _jwks(settings: Settings) -> PyJWKClient:
    """The cached JWKS client. Built once, on first use, not at import time —
    importing this module must not make a network call."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            settings.supabase_jwks_url,
            cache_keys=True,
            lifespan=600,  # re-fetch every 10 minutes at most
        )
    return _jwks_client


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> uuid.UUID:
    """The signed-in user's id, or 401.

    Returns the Supabase `sub` claim as a UUID — the same value as
    `auth.users.id`, and the value every row in this domain is scoped by.
    """
    if not settings.auth_configured:
        # Refuse rather than wave everyone through. A misconfigured deploy that
        # silently served everyone's history to everyone would be far worse
        # than one that plainly does not work.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Authentication is not configured on this server "
                "(SUPABASE_PROJECT_REF is unset)."
            ),
        )

    if credentials is None or not credentials.credentials:
        raise _unauthorized("Sign in first: send a Supabase access token as "
                            "'Authorization: Bearer <token>'.")

    token = credentials.credentials
    try:
        # In a worker thread, not on the event loop.
        #
        # PyJWKClient fetches over blocking urllib. Called directly from an
        # async handler that stops the entire server for the duration — not
        # just this request — so one slow fetch stalls every other user's
        # request too. It is cached after the first call, but "usually fast"
        # is not the same as "cannot block", and a key rotation makes it
        # happen again at a moment nobody chose.
        signing_key = await asyncio.to_thread(
            _jwks(settings).get_signing_key_from_jwt, token
        )
    except PyJWKClientConnectionError as exc:
        # We could not reach Supabase to fetch the signing keys. The token may
        # be perfectly good — we simply cannot tell. Saying 401 here would log
        # every user out of a working app because of someone else's outage, so
        # this is a 503: "try again", not "you are not who you say you are".
        logger.error("could not fetch Supabase JWKS: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot verify sign-in right now. Please try again.",
        ) from None
    except PyJWKClientError as exc:
        # No key matching this token's `kid` — an unsigned, forged or
        # foreign-project token. PyJWKClientError is NOT a subclass of
        # InvalidTokenError, so it needs saying separately or it escapes as a
        # 500 and tells the caller the server broke rather than their token.
        logger.warning("no signing key for presented token: %s", exc)
        raise _unauthorized("That access token is not valid.") from None
    except jwt.InvalidTokenError as exc:
        # Not a JWT at all — garbage, truncated, or a different kind of token.
        # Finding the key means parsing the header first, and that raises
        # DecodeError before any signature is checked; uncaught it was a 500.
        logger.warning("rejected a malformed token: %s", exc)
        raise _unauthorized("That access token is not valid.") from None

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            # Supabase stamps every access token with this audience. Checking
            # it stops a token minted for some other Supabase surface being
            # replayed here.
            audience="authenticated",
            issuer=settings.supabase_issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Your session has expired. Sign in again.") from None
    except jwt.InvalidTokenError as exc:
        # Deliberately vague to the caller, specific in the log: telling an
        # attacker *which* part of their forged token was wrong is free help.
        logger.warning("rejected a token: %s", exc)
        raise _unauthorized("That access token is not valid.") from None

    sub = claims.get("sub")
    try:
        return uuid.UUID(str(sub))
    except (TypeError, ValueError):
        logger.warning("token carried a non-UUID sub: %r", sub)
        raise _unauthorized("That access token is not valid.") from None


async def require_service(
    x_service_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Gate for the internal routes the agent calls. Raises unless the secret
    matches.

    Compared with `hmac.compare_digest` rather than `==`: a plain comparison
    returns as soon as two bytes differ, and the time that takes is enough to
    guess a secret one character at a time over enough requests.

    An unset `service_token` disables these routes instead of opening them.
    Leaving a default in place is how internal endpoints end up public.
    """
    if not settings.service_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal routes are disabled (SERVICE_TOKEN is unset).",
        )
    if not service_token_valid(x_service_token, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bad or missing X-Service-Token.",
        )


def service_token_valid(presented: str | None, settings: Settings) -> bool:
    """Whether `presented` is this deployment's service token. Constant time."""
    return bool(
        settings.service_token
        and presented
        and hmac.compare_digest(presented, settings.service_token)
    )


async def user_or_service(
    x_service_token: str | None = Header(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    """Gate for routes both the agent and a signed-in person may call.

    Trip search is the case: the agent searches mid-call on the user's behalf,
    and the app may one day search directly. Either key works; neither is
    optional. Returns a stable caller key — "service" or "user:<id>" — which is
    what the rate limiter counts against.

    A service token that is present but wrong is a 401 rather than a fall
    through to the JWT: a caller that thinks it is the agent and is not should
    hear about it, not be told to sign in.
    """
    if x_service_token is not None:
        if service_token_valid(x_service_token, settings):
            return "service"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bad X-Service-Token.",
        )
    user_id = await current_user(credentials=credentials, settings=settings)
    return f"user:{user_id}"


async def warm_jwks(settings: Settings) -> None:
    """Fetch the signing keys at startup, so no user's request pays for it.

    Whoever arrives first would otherwise wait for a round trip to Supabase on
    top of everything else their request does — and on a cold server that lands
    on the same request as the first database connection, which is how a
    perfectly healthy backend produced a timeout in the app.

    Failure is ignored: the keys will be fetched on demand, which is what used
    to happen anyway. This is an optimisation, not a dependency.
    """
    if not settings.auth_configured:
        return
    try:
        await asyncio.to_thread(_jwks(settings).get_jwk_set)
        logger.info("Supabase signing keys cached")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not pre-fetch Supabase signing keys: %s", exc)
