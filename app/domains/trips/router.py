"""HTTP routes for trips: search endpoints (called by the agent's tools and the
app) and the saved-trips endpoints.

Note the two shapes of route:
  - search routes need no DB — they call the service, which calls a provider.
    The agent calls them with its service token; a signed-in person may call
    them with their JWT. Nobody else may, because each one can spend the
    flight API's shared 200-an-hour budget.
  - saved-trip routes depend on `get_db_session`, build a repository, and pass
    it into the service. They belong to a signed-in person and are scoped to
    them: the owner is the JWT's user id, never a value the caller sends.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
from app.shared.auth import current_user, user_or_service
from app.shared.exceptions import NotFoundError
from app.shared.rate_limit import RateLimiter

router = APIRouter(prefix="/trips", tags=["trips"])
_service = TripService()

_settings = get_settings()
search_limiter = RateLimiter(
    limit=_settings.search_limit,
    window=_settings.search_window_seconds,
    name="search",
)


async def _search_caller(caller: str = Depends(user_or_service)) -> str:
    # The agent is exempt — see `search_limit` in config.
    if caller != "service":
        search_limiter.check(caller)
    return caller


# --- search ----------------------------------------------------------------
@router.post(
    "/search/trains",
    response_model=list[TransitOption],
    dependencies=[Depends(_search_caller)],
)
async def search_trains(q: TransitSearch) -> list[TransitOption]:
    return await _service.search_trains(q)


@router.post(
    "/search/flights",
    response_model=list[TransitOption],
    dependencies=[Depends(_search_caller)],
)
async def search_flights(q: TransitSearch) -> list[TransitOption]:
    return await _service.search_flights(q)


@router.post(
    "/search/stays",
    response_model=list[StayOption],
    dependencies=[Depends(_search_caller)],
)
async def search_stays(q: StaySearch) -> list[StayOption]:
    return await _service.search_stays(q)


# --- saved trips (DB, per user) ----------------------------------------------
@router.post("/saved", response_model=SavedTripOut, status_code=201)
async def save_trip(
    body: SaveTripRequest,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SavedTripOut:
    """Save a trip for the signed-in user. Any `userRef` in the body is
    ignored: the owner is whoever the token says, not whoever the body says."""
    return await _service.save_trip(TripRepository(db), body, owner=str(user_id))


@router.get("/saved", response_model=list[SavedTripOut])
async def list_my_saved_trips(
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[SavedTripOut]:
    """The signed-in user's saved trips, newest first."""
    return await _service.list_saved_trips(TripRepository(db), str(user_id))


@router.get("/saved/{user_ref}", response_model=list[SavedTripOut], deprecated=True)
async def list_saved_trips(
    user_ref: str,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[SavedTripOut]:
    """Kept for older clients. Only ever answers about the caller: asking for
    somebody else's list gets an empty one, not theirs. Use `GET /saved`."""
    if user_ref != str(user_id):
        return []
    return await _service.list_saved_trips(TripRepository(db), str(user_id))


@router.delete("/saved/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_trip(
    trip_id: int,
    user_id: uuid.UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Remove one of the signed-in user's saved trips."""
    deleted = await _service.delete_saved_trip(
        TripRepository(db), trip_id=trip_id, owner=str(user_id)
    )
    if not deleted:
        raise NotFoundError("That saved trip could not be found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
