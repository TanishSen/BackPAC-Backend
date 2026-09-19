"""The shape of the spoken greeting the welcome screen plays."""

from pydantic import BaseModel, ConfigDict, Field


class GreetingOut(BaseModel):
    """One line the orb can say.

    `levels` is one loudness value (0..1) per `frameMs`, so the app can drive
    the orb's mouth from the actual waveform rather than a generic wobble. It is
    a few hundred bytes and travels inline.

    The audio does not: fetch it from `GET /api/v1/voice/line.wav?text=…`.
    Inlining it as base64 turned the welcome set into a 2.5 MB body that no
    cache could reuse; as a file it is cached and downloaded once per line.
    """

    model_config = ConfigDict(populate_by_name=True)

    text: str
    levels: list[float]
    frame_ms: int = Field(alias="frameMs")


class WelcomeLinesOut(BaseModel):
    """Everything the orb can say on the welcome screen, in one response.

    The app fetches this once and keeps it, so poking the orb answers instantly
    rather than after a round trip. Each entry is the same shape as
    [GreetingOut].
    """

    model_config = ConfigDict(populate_by_name=True)

    greeting: list[GreetingOut]
    poke: list[GreetingOut]
    idle: list[GreetingOut]
