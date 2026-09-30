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

import logging
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.domains.trips.providers.base import FlightProvider, ProviderError
from app.domains.trips.providers.mock import MockFlights
from app.domains.trips.providers.travelpayouts import TravelpayoutsFlights
from app.domains.trips.repository import TripRepository
from app.domains.trips.schemas import (
    SaveTripRequest,
    StayOption,
    StaySearch,
    TransitOption,
    TransitSearch,
)


logger = logging.getLogger(__name__)


def _default_flight_provider() -> FlightProvider:
    """The real provider when configured, sample data otherwise.

    Chosen once at construction rather than per request, so a deployment
    cannot silently drift between real and sample answers.
    """
    settings = get_settings()
    if settings.flights_live:
        return TravelpayoutsFlights(
            token=settings.travelpayouts_token,
            marker=settings.travelpayouts_marker,
        )
    logger.warning(
        "no TRAVELPAYOUTS_TOKEN — serving sample flights, clearly labelled"
    )
    return MockFlights()


class TripService:
    """Composes a search out of whichever provider is configured.

    The provider is injected rather than constructed here, so the service has
    no opinion about which company answers and tests can pass a fake. See
    `providers/` and `deps.py`.
    """

    def __init__(self, flights: FlightProvider | None = None):
        self._flights = flights or _default_flight_provider()

    # --- search --------------------------------------------------------
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
        """Flights for this route, around this date. May legitimately be empty.

        A provider failure becomes an empty list rather than an exception, and
        is logged loudly. The reasoning: the agent is mid-conversation with a
        person, and "I could not find flights just now" is a recoverable turn,
        while a 502 ends the call. The log is where an operator finds out;
        the caller is not the right place to raise an alarm.
        """
        try:
            options = await self._flights.search_flights(q)
        except ProviderError as exc:
            logger.warning(
                "flight search failed via %s (%s -> %s): %s",
                getattr(self._flights, "name", "?"),
                q.origin,
                q.destination,
                exc,
            )
            return []
        except Exception:  # noqa: BLE001 — a provider must not end a call
            logger.exception(
                "flight provider %s raised unexpectedly",
                getattr(self._flights, "name", "?"),
            )
            return []

        logger.info(
            "flights %s -> %s on %s: %d option(s) via %s",
            q.origin,
            q.destination,
            q.depart_date,
            len(options),
            getattr(self._flights, "name", "?"),
        )
        return options

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
    async def save_trip(
        self, repo: TripRepository, body: SaveTripRequest, *, owner: str
    ):
        return await repo.add_saved_trip(
            user_ref=owner,
            title=body.title,
            destination=body.destination,
            nights=body.nights,
        )

    async def list_saved_trips(self, repo: TripRepository, user_ref: str):
        return await repo.list_saved_trips(user_ref)

    async def delete_saved_trip(
        self, repo: TripRepository, *, trip_id: int, owner: str
    ) -> bool:
        return await repo.delete_saved_trip(trip_id=trip_id, user_ref=owner)
