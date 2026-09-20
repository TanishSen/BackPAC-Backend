"""Sample flights, for when no provider is configured.

Not a fallback for a provider that failed — that case returns empty and says
so. This is for a laptop with no API token: the app, the agent and the tests
all need *something* flight-shaped, and a clearly fictional flight is more
honest than an error screen while someone is building a UI.

Everything it returns is marked `price_is_approximate`, and the airline is
named "Sample airline" rather than IndiGo, so a demo can never be mistaken for
a real quote in a screenshot.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domains.trips.schemas import TransitOption, TransitSearch


class MockFlights:
    name = "mock"

    async def search_flights(self, q: TransitSearch) -> list[TransitOption]:
        base = datetime.combine(q.depart_date, datetime.min.time()).replace(hour=9)
        return [
            TransitOption(
                provider="Sample airline",
                name="XX-000",
                depart=base.isoformat(),
                arrive=(base + timedelta(hours=1, minutes=10)).isoformat(),
                duration_minutes=70,
                price_inr=4200,
                stops=0,
                booking_url="",
                price_is_approximate=True,
            ),
        ]
