"""Flights, from Travelpayouts' Aviasales data API.

**What this source is.** Cached metasearch prices — what Aviasales users
recently found — not a live availability query against an airline. That single
fact shapes everything here:

- **Prices are approximate.** Each row carries `expires_at`; past it the number
  is a memory. The agent is expected to say "around ₹21,000", never "₹21,000".
- **Not every date has data.** Dense routes (DEL–BOM) have most days; thin ones
  may have four days in a quarter and none in the month you asked for. An empty
  answer here means "this source has no price", not "there are no flights", and
  the wording upstream reflects that.
- **The date is a hint, not a filter.** The provider ignores `depart_date` on
  the calendar endpoint, so the filtering is done here: exact day if we have
  it, otherwise the nearest days either side, which is the genuinely useful
  answer for someone planning rather than booking.

**Coverage**, verified live against Indian domestic routes: 6E IndiGo, AI Air
India, IX Air India Express, SG SpiceJet, QP Akasa. Prices come back in INR
natively, so nothing is converted.

**Rate limit: 200 requests/hour per IP**, shared across the whole deployment.
Every response is cached — see `_Cache` — because a conversation asks about the
same route repeatedly and the underlying data only changes daily.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.domains.trips.providers.base import ProviderError
from app.domains.trips.providers.iata import to_iata
from app.domains.trips.schemas import TransitOption, TransitSearch

logger = logging.getLogger(__name__)

API_ROOT = "https://api.travelpayouts.com"

#: How long a route's calendar is reused. The upstream data is refreshed daily
#: at best, so a shorter TTL spends the rate limit without buying freshness.
CACHE_TTL_SECONDS = 60 * 60 * 6

#: How far either side of the requested day to offer alternatives when the day
#: itself has no price. Wider than this stops being "around then".
NEARBY_DAYS = 10

#: Most expensive thing we will call a result. Beyond this the list is noise.
MAX_RESULTS = 5

#: IATA airline code -> the name a person would say. The API returns codes;
#: "IndiGo 6E-634" reads better than "6E 634", and the agent speaks this text.
AIRLINES: dict[str, str] = {
    "6E": "IndiGo",
    "AI": "Air India",
    "IX": "Air India Express",
    "SG": "SpiceJet",
    "QP": "Akasa Air",
    "UK": "Vistara",
    "G8": "Go First",
    "I5": "AIX Connect",
    "EK": "Emirates",
    "EY": "Etihad",
    "QR": "Qatar Airways",
    "SQ": "Singapore Airlines",
    "TG": "Thai Airways",
    "UL": "SriLankan Airlines",
    "FZ": "flydubai",
    "AK": "AirAsia",
}


@dataclass(frozen=True)
class _Entry:
    value: list[dict[str, Any]]
    expires_at: float


class _Cache:
    """A small TTL cache, keyed by route.

    In-process on purpose, and that is a deliberate limit worth stating: with
    several API containers each keeps its own copy, so the effective rate-limit
    spend multiplies by the replica count. One container is fine; past two or
    three, move this to Redis — the interface is two methods precisely so that
    swap stays small.
    """

    def __init__(self) -> None:
        self._data: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> list[dict[str, Any]] | None:
        async with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            if hit.expires_at < time.monotonic():
                # Drop rather than serve stale: the point of the TTL is that
                # the price stopped being worth quoting.
                del self._data[key]
                return None
            return hit.value

    async def put(self, key: str, value: list[dict[str, Any]]) -> None:
        async with self._lock:
            self._data[key] = _Entry(
                value=value, expires_at=time.monotonic() + CACHE_TTL_SECONDS
            )

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()


class TravelpayoutsFlights:
    """Implements `FlightProvider` against the Aviasales data API."""

    name = "travelpayouts"

    def __init__(
        self,
        *,
        token: str,
        marker: str,
        http: httpx.AsyncClient | None = None,
        timeout: float = 8.0,
    ):
        if not token:
            raise ValueError("Travelpayouts needs a token")
        self._token = token
        self._marker = marker
        self._http = http
        self._timeout = timeout
        self._cache = _Cache()

    # --- the one method the service calls ---------------------------------

    async def search_flights(self, q: TransitSearch) -> list[TransitOption]:
        origin = to_iata(q.origin)
        destination = to_iata(q.destination)
        if origin is None or destination is None:
            # Not an error: we simply do not know that airport. Saying so
            # beats searching a route nobody asked about.
            logger.info(
                "no IATA code for %r -> %r",
                q.origin if origin is None else q.destination,
                None,
            )
            return []
        if origin == destination:
            return []

        rows = await self._calendar(origin, destination)
        if not rows:
            return []

        picked = self._closest_to(rows, q.depart_date)
        return [self._to_option(r, origin, destination) for r in picked]

    # --- talking to the provider ------------------------------------------

    async def _calendar(self, origin: str, destination: str) -> list[dict]:
        """Every dated price this source has for the route. Cached."""
        key = f"{origin}-{destination}"
        cached = await self._cache.get(key)
        if cached is not None:
            logger.debug("travelpayouts cache hit for %s", key)
            return cached

        params = {
            "origin": origin,
            "destination": destination,
            "currency": "inr",
            "token": self._token,
        }
        body = await self._get("/v1/prices/calendar", params)

        data = body.get("data")
        if not isinstance(data, dict):
            return []
        # The response is keyed by date; the date is only inside the value on
        # some endpoints, so it is copied in here and relied on from one place.
        rows = [{**v, "_date": k} for k, v in data.items() if isinstance(v, dict)]
        await self._cache.put(key, rows)
        return rows

    async def _get(self, path: str, params: dict) -> dict:
        """One GET, with the failures named.

        Distinguishes "try again" from "this will never work", because the
        caller logs and alerts on them differently — and a 429 on a shared
        200/hour budget is an operational fact, not a bug.
        """
        url = f"{API_ROOT}{path}"
        client = self._http
        owned = client is None
        if owned:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError("flight search timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"flight search failed: {exc}", retryable=True) from exc
        finally:
            if owned:
                await client.aclose()

        if resp.status_code == 429:
            raise ProviderError(
                "flight search is rate limited (200/hour)", retryable=True
            )
        if resp.status_code in (401, 403):
            # Not retryable: the token is wrong, and it will be wrong next time.
            raise ProviderError("flight search rejected our token")
        if resp.status_code >= 500:
            raise ProviderError("the flight provider is down", retryable=True)
        if resp.status_code >= 400:
            raise ProviderError(f"flight search refused ({resp.status_code})")

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("flight provider sent malformed JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError("flight provider sent an unexpected shape")
        return body

    # --- shaping ----------------------------------------------------------

    @staticmethod
    def _closest_to(rows: list[dict], wanted: date) -> list[dict]:
        """The requested day if it exists, otherwise the nearest ones.

        Sorted by how far from the asked-for date they are, then by price, so
        the first result is the closest match and the rest are honest
        alternatives — which is the whole value of a cached fare calendar.
        """
        dated: list[tuple[int, int, dict]] = []
        for r in rows:
            try:
                day = datetime.fromisoformat(r["departure_at"]).date()
            except (KeyError, ValueError):
                continue
            distance = abs((day - wanted).days)
            if distance > NEARBY_DAYS:
                continue
            dated.append((distance, int(r.get("price", 0)), r))

        if not dated:
            return []
        dated.sort(key=lambda t: (t[0], t[1]))
        return [r for _, _, r in dated[:MAX_RESULTS]]

    def _to_option(self, row: dict, origin: str, destination: str) -> TransitOption:
        code = str(row.get("airline", "")).upper()
        flight_no = row.get("flight_number")
        departure = str(row.get("departure_at", ""))

        # `duration` on this endpoint covers the round trip when one is
        # present, so it is not a one-way flight time and is not reported as
        # one. Absent is better than wrong.
        arrive = ""
        duration = int(row.get("duration_to") or 0)
        if duration and departure:
            try:
                arrive = (
                    datetime.fromisoformat(departure) + timedelta(minutes=duration)
                ).isoformat()
            except ValueError:
                arrive = ""

        return TransitOption(
            provider=AIRLINES.get(code, code or "Airline"),
            name=f"{code}-{flight_no}" if code and flight_no else str(flight_no or ""),
            depart=departure,
            arrive=arrive,
            duration_minutes=duration,
            price_inr=int(row.get("price", 0)),
            stops=int(row.get("transfers", 0) or 0),
            booking_url=self._deeplink(origin, destination, departure),
            price_is_approximate=True,
        )

    def _deeplink(self, origin: str, destination: str, departure: str) -> str:
        """Where tapping the card sends someone — carrying our marker.

        The marker is what turns a click into commission; without it the
        affiliate model earns nothing, which is the entire reason this provider
        was chosen over a paid one.
        """
        try:
            day = datetime.fromisoformat(departure).strftime("%d%m")
        except ValueError:
            day = ""
        return (
            f"https://www.aviasales.com/search/{origin}{day}{destination}1"
            f"?marker={self._marker}"
        )
