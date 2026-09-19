"""Business logic for trips.

Two kinds of work live here:

1. **Search** (trains / flights / stays) — today these return MOCK data so the
   app and agent have something real-shaped to call. This is where the actual
   provider integrations go: IRCTC/RapidAPI for trains, an airline/aggregator
   API for flights, a hotel API for stays. Swap the mock body for an HTTP call
   to that provider and return the same schema — nothing above this changes.

2. **Saved trips** — the DB-backed example, showing the full
   router -> service -> repository -> DB path.

The search methods take no DB session because they hit external providers, not
our database. That is normal: a service composes whatever a use case needs.
"""

from datetime import datetime, timedelta

from app.domains.trips.repository import TripRepository
from app.domains.trips.schemas import (
    SaveTripRequest,
    StayOption,
    StaySearch,
    TransitOption,
    TransitSearch,
)


class TripService:
    # --- search: MOCK for now, real providers later --------------------
    async def search_trains(self, q: TransitSearch) -> list[TransitOption]:
        # TODO(backend): replace with the real train provider (IRCTC/RapidAPI).
        # Keep the return type; the agent and app depend on this shape.
        base = datetime.combine(q.depart_date, datetime.min.time()).replace(hour=6)
        return [
            TransitOption(
                provider="IRCTC",
                name="Shatabdi Express",
                depart=base.isoformat(),
                arrive=(base + timedelta(hours=4, minutes=35)).isoformat(),
                durationMinutes=275,
                priceInr=1240,
            ),
            TransitOption(
                provider="IRCTC",
                name="Double Decker",
                depart=(base + timedelta(hours=8)).isoformat(),
                arrive=(base + timedelta(hours=12, minutes=50)).isoformat(),
                durationMinutes=290,
                priceInr=890,
            ),
        ]

    async def search_flights(self, q: TransitSearch) -> list[TransitOption]:
        # TODO(backend): real flight provider.
        base = datetime.combine(q.depart_date, datetime.min.time()).replace(hour=9)
        return [
            TransitOption(
                provider="IndiGo",
                name="6E-2043",
                depart=base.isoformat(),
                arrive=(base + timedelta(hours=1, minutes=10)).isoformat(),
                durationMinutes=70,
                priceInr=4200,
            ),
        ]

    async def search_stays(self, q: StaySearch) -> list[StayOption]:
        # TODO(backend): real hotel provider.
        return [
            StayOption(
                name="Haveli Courtyard",
                area="Old City",
                rating=4.6,
                pricePerNightInr=2900,
            ),
            StayOption(
                name="Pink City Suites",
                area="Bani Park",
                rating=4.3,
                pricePerNightInr=3400,
            ),
        ]

    # --- saved trips: the DB-backed path -------------------------------
    async def save_trip(self, repo: TripRepository, body: SaveTripRequest):
        return await repo.add_saved_trip(
            user_ref=body.user_ref,
            title=body.title,
            destination=body.destination,
            nights=body.nights,
        )

    async def list_saved_trips(self, repo: TripRepository, user_ref: str):
        return await repo.list_saved_trips(user_ref)
