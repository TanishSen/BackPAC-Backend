"""Collects every domain router under one prefix: /api/v1.

When you add a domain (say `bookings`), write its router.py and add one line
here. Nothing else in the app needs to know it exists.
"""

from fastapi import APIRouter

from app.domains.account.router import me_router
from app.domains.account.router import router as account_router
from app.domains.billing.router import router as billing_router
from app.domains.sessions.router import groups_router
from app.domains.sessions.router import router as sessions_router
from app.domains.trips.router import router as trips_router
from app.domains.voice.router import router as voice_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(account_router)
api_router.include_router(me_router)
api_router.include_router(billing_router)
api_router.include_router(groups_router)
api_router.include_router(sessions_router)
api_router.include_router(trips_router)
api_router.include_router(voice_router)
