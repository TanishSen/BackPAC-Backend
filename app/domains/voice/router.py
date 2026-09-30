"""HTTP routes for voice extras. Thin, like every router here."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.core.config import Settings, get_settings
from app.domains.voice.schemas import GreetingOut, WelcomeLinesOut
from app.domains.voice.service import VoiceService
from app.shared.rate_limit import RateLimiter, limit_by_ip

_settings = get_settings()
voice_limiter = RateLimiter(
    limit=_settings.voice_limit,
    window=_settings.voice_window_seconds,
    name="voice",
)

# Public on purpose — the welcome screen plays before anyone signs in — so the
# limit is per IP. The agent also refuses any text that is not one of its own
# lines, which is what actually stops this being a free text-to-speech API.
router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    dependencies=[Depends(limit_by_ip(voice_limiter))],
)

#: The longest line the agent says is well under this.
_TEXT = Query(max_length=200)


@router.get("/greeting", response_model=GreetingOut)
async def greeting(
    request: Request,
    text: str | None = Query(default=None, max_length=200),
    settings: Settings = Depends(get_settings),
) -> GreetingOut:
    """The spoken hello for the welcome screen, with a level track for the orb.

    `text` is optional — leave it off and the agent picks one of its lines, so
    the app doesn't open with the identical sentence every single launch.
    """
    service = VoiceService(request.app.state.http_client, settings)
    return await service.greeting(text)


@router.get("/welcome-lines", response_model=WelcomeLinesOut)
async def welcome_lines(
    request: Request, settings: Settings = Depends(get_settings)
) -> WelcomeLinesOut:
    """Everything the orb can say on the welcome screen, audio included.

    Fetched once by the app and kept, so tapping the orb gets a reply with no
    round trip in the way.
    """
    service = VoiceService(request.app.state.http_client, settings)
    return await service.welcome_lines()


@router.get("/line.wav")
async def line_audio(
    request: Request,
    text: str = _TEXT,
    settings: Settings = Depends(get_settings),
) -> Response:
    """The audio for one spoken line.

    A file, not base64 in a JSON body, so the browser and the OS cache it. The
    text fully determines the audio, so the response is marked immutable and
    never fetched twice.
    """
    service = VoiceService(request.app.state.http_client, settings)
    audio = await service.line_audio(text)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
