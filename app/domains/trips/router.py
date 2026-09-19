"""HTTP routes for trips: search endpoints (called by the agent's tools and the
app) and the saved-trips endpoints (the DB example).

Note the two shapes of route:
  - search routes need no DB — they call the service, which calls a provider.
  - saved-trip routes depend on `get_db_session`, build a repository, and pass
    it into the service. That is the full layered path, start to finish.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.domains.trips.repository import TripRepository
from app.domains.trips.schemas import (
    SavedTripOut,
    SaveTripRequest,
    StayOption,
    StaySearch,
    TransitOption,
    TransitSearch,
)
from app.domains.trips.service import TripService

router = APIRouter(prefix="/trips", tags=["trips"])
_service = TripService()


# --- search ----------------------------------------------------------------
@router.post("/search/trains", response_model=list[TransitOption])
async def search_trains(q: TransitSearch) -> list[TransitOption]:
    return await _service.search_trains(q)


@router.post("/search/flights", response_model=list[TransitOption])
async def search_flights(q: TransitSearch) -> list[TransitOption]:
    return await _service.search_flights(q)


@router.post("/search/stays", response_model=list[StayOption])
async def search_stays(q: StaySearch) -> list[StayOption]:
    return await _service.search_stays(q)


# --- saved trips (DB) ------------------------------------------------------
@router.post("/saved", response_model=SavedTripOut, status_code=201)
async def save_trip(
    body: SaveTripRequest, db: AsyncSession = Depends(get_db_session)
) -> SavedTripOut:
    return await _service.save_trip(TripRepository(db), body)


@router.get("/saved/{user_ref}", response_model=list[SavedTripOut])
async def list_saved_trips(
    user_ref: str, db: AsyncSession = Depends(get_db_session)
) -> list[SavedTripOut]:
    return await _service.list_saved_trips(TripRepository(db), user_ref)
