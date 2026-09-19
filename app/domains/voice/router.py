"""HTTP routes for voice extras. Thin, like every router here."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import get_settings
from app.domains.voice.schemas import GreetingOut, WelcomeLinesOut
from app.domains.voice.service import VoiceService

router = APIRouter(prefix="/voice", tags=["voice"])


@router.get("/greeting", response_model=GreetingOut)
async def greeting(request: Request, text: str | None = None) -> GreetingOut:
    """The spoken hello for the welcome screen, with a level track for the orb.

    `text` is optional — leave it off and the agent picks one of its lines, so
    the app doesn't open with the identical sentence every single launch.
    """
    service = VoiceService(request.app.state.http_client, get_settings())
    return await service.greeting(text)


@router.get("/welcome-lines", response_model=WelcomeLinesOut)
async def welcome_lines(request: Request) -> WelcomeLinesOut:
    """Everything the orb can say on the welcome screen, audio included.

    Fetched once by the app and kept, so tapping the orb gets a reply with no
    round trip in the way.
    """
    service = VoiceService(request.app.state.http_client, get_settings())
    return await service.welcome_lines()


@router.get("/line.wav")
async def line_audio(request: Request, text: str) -> Response:
    """The audio for one spoken line.

    A file, not base64 in a JSON body, so the browser and the OS cache it. The
    text fully determines the audio, so the response is marked immutable and
    never fetched twice.
    """
    service = VoiceService(request.app.state.http_client, get_settings())
    audio = await service.line_audio(text)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
