"""The contract every flight provider meets.

A Protocol rather than a base class: providers share no implementation, only a
shape, and `TripService` should accept anything that fits — including the fake
used in tests, which inherits from nothing.

**Providers do not raise for "nothing found".** An empty list is a real answer
and the agent knows how to say it. They raise `ProviderError` only when the
provider itself failed — unreachable, throttled, malformed — because that is a
different thing for the caller to say and a different thing to alert on.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domains.trips.schemas import TransitOption, TransitSearch


class ProviderError(RuntimeError):
    """The provider could not answer. Not the same as "no flights"."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        #: Whether trying again shortly might work — a timeout or a 429, as
        #: opposed to a malformed request that will fail identically forever.
        self.retryable = retryable


@runtime_checkable
class FlightProvider(Protocol):
    """Finds flights. Implementations live beside this file."""

    #: Shown in logs and metrics so it is obvious which provider answered.
    name: str

    async def search_flights(self, q: TransitSearch) -> list[TransitOption]:
        """Options for this route, cheapest first. May be empty."""
        ...
