"""The only layer that runs SQL for the trips domain.

A repository turns "save this trip" / "list this user's trips" into queries and
hands back ORM objects or plain data. It never decides *whether* to do
something (that is the service's job) — it just does the storage.

Why bother, on a hackathon? Because when the demo works and someone asks to add
caching, or swap Postgres for something else, it changes here and nowhere else.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.trips.models import SavedTrip


class TripRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def add_saved_trip(
        self, *, user_ref: str, title: str, destination: str, nights: int
    ) -> SavedTrip:
        trip = SavedTrip(
            user_ref=user_ref,
            title=title,
            destination=destination,
            nights=nights,
        )
        self._db.add(trip)
        await self._db.commit()
        await self._db.refresh(trip)
        return trip

    async def list_saved_trips(self, user_ref: str) -> Sequence[SavedTrip]:
        result = await self._db.execute(
            select(SavedTrip)
            .where(SavedTrip.user_ref == user_ref)
            .order_by(SavedTrip.created_at.desc())
        )
        return result.scalars().all()
