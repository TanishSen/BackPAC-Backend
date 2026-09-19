"""Wire shapes for the trips domain — what the app and the agent send/receive.

Schemas (Pydantic) are NOT the same as models (SQLAlchemy). Models are how data
is stored; schemas are how it travels over HTTP. Keeping them separate means
you can change the table without changing the API and vice versa.

The search request/result shapes below are also exactly what the AGENT calls as
its tools (search_trains / search_flights / search_stays). So this file is the
contract for both the app and the agent.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


# --- search: request -------------------------------------------------------
class TransitSearch(BaseModel):
    """Shared by trains, flights and buses — same shape, different provider."""

    origin: str = Field(examples=["Delhi"])
    destination: str = Field(examples=["Jaipur"])
    depart_date: date = Field(alias="departDate")
    passengers: int = Field(default=1, ge=1, le=9)

    model_config = ConfigDict(populate_by_name=True)


class StaySearch(BaseModel):
    destination: str
    check_in: date = Field(alias="checkIn")
    check_out: date = Field(alias="checkOut")
    guests: int = Field(default=2, ge=1, le=12)

    model_config = ConfigDict(populate_by_name=True)


# --- search: results -------------------------------------------------------
class TransitOption(BaseModel):
    provider: str  # e.g. "IRCTC", "IndiGo"
    name: str  # "Shatabdi Express", "6E-2043"
    depart: str  # ISO time string
    arrive: str
    duration_minutes: int = Field(alias="durationMinutes")
    price_inr: int = Field(alias="priceInr")

    model_config = ConfigDict(populate_by_name=True)


class StayOption(BaseModel):
    name: str
    area: str
    rating: float
    price_per_night_inr: int = Field(alias="pricePerNightInr")

    model_config = ConfigDict(populate_by_name=True)


# --- saved trips (the DB-backed example) -----------------------------------
class SaveTripRequest(BaseModel):
    user_ref: str = Field(alias="userRef")
    title: str
    destination: str
    nights: int = Field(default=1, ge=1)

    model_config = ConfigDict(populate_by_name=True)


class SavedTripOut(BaseModel):
    id: int
    title: str
    destination: str
    nights: int

    # from_attributes lets FastAPI build this straight from the ORM object.
    model_config = ConfigDict(from_attributes=True)
