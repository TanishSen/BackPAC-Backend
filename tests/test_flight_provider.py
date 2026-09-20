"""The Travelpayouts provider: date picking, failures, and what it promises.

The provider talks to a cached metasearch feed, so most of its job is deciding
which of the days it happens to have are worth offering — and being honest that
the prices are estimates. Both are tested here against a stubbed HTTP layer, so
nothing in this file touches the network or spends the rate limit.
"""

from datetime import date

import httpx
import pytest
import respx

from app.domains.trips.providers.base import ProviderError
from app.domains.trips.providers.travelpayouts import (
    API_ROOT,
    TravelpayoutsFlights,
)
from app.domains.trips.schemas import TransitSearch

CALENDAR = f"{API_ROOT}/v1/prices/calendar"


def provider(**kw) -> TravelpayoutsFlights:
    return TravelpayoutsFlights(token="test-token", marker="779956", **kw)


def query(origin="Kolkata", destination="Goa", day=date(2026, 12, 25)):
    return TransitSearch(origin=origin, destination=destination, departDate=day)


def calendar_body(rows: dict) -> dict:
    return {"data": rows, "currency": "inr", "success": True}


def row(day: str, *, airline="6E", price=21229, number=634, transfers=0):
    return {
        "origin": "CCU",
        "destination": "GOI",
        "airline": airline,
        "departure_at": f"{day}T10:30:00+05:30",
        "price": price,
        "flight_number": number,
        "transfers": transfers,
        "duration_to": 175,
    }


@respx.mock
async def test_exact_date_wins_over_a_cheaper_nearby_one():
    respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200,
            json=calendar_body(
                {
                    # Cheaper, but not the day that was asked for.
                    "2026-12-23": row("2026-12-23", price=9000),
                    "2026-12-25": row("2026-12-25", price=21229),
                }
            ),
        )
    )
    out = await provider().search_flights(query())
    assert out[0].depart.startswith("2026-12-25")
    assert out[0].price_inr == 21229
    # The cheaper day is still offered, just second — "shift your dates" is
    # the useful thing a fare calendar can say.
    assert out[1].depart.startswith("2026-12-23")


@respx.mock
async def test_nearby_days_when_the_exact_date_has_no_price():
    respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200, json=calendar_body({"2026-12-28": row("2026-12-28")})
        )
    )
    out = await provider().search_flights(query())
    assert len(out) == 1
    assert out[0].depart.startswith("2026-12-28")


@respx.mock
async def test_distant_dates_are_not_offered_as_alternatives():
    # A price in October is not an answer to a question about December.
    respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200, json=calendar_body({"2026-10-17": row("2026-10-17")})
        )
    )
    assert await provider().search_flights(query()) == []


@respx.mock
async def test_airline_codes_become_names_people_say():
    respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200,
            json=calendar_body({"2026-12-25": row("2026-12-25", airline="6E")}),
        )
    )
    out = await provider().search_flights(query())
    assert out[0].provider == "IndiGo"
    assert out[0].name == "6E-634"


@respx.mock
async def test_an_unknown_airline_code_is_kept_rather_than_dropped():
    respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200,
            json=calendar_body({"2026-12-25": row("2026-12-25", airline="ZZ")}),
        )
    )
    out = await provider().search_flights(query())
    assert out[0].provider == "ZZ"


@respx.mock
async def test_the_price_is_always_flagged_as_an_estimate():
    # The data is cached metasearch, so a quoted price is a memory. The agent
    # relies on this flag to say "around" rather than a firm number.
    respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200, json=calendar_body({"2026-12-25": row("2026-12-25")})
        )
    )
    out = await provider().search_flights(query())
    assert out[0].price_is_approximate is True


@respx.mock
async def test_the_booking_link_carries_the_marker():
    # Without the marker a click earns nothing, which is the entire reason
    # this provider was chosen over a paid one.
    respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200, json=calendar_body({"2026-12-25": row("2026-12-25")})
        )
    )
    out = await provider().search_flights(query())
    assert "marker=779956" in out[0].booking_url
    assert "CCU" in out[0].booking_url and "GOI" in out[0].booking_url


async def test_an_unknown_city_asks_the_provider_nothing():
    # No HTTP mock is installed, so any request would fail the test — the
    # point being that we do not spend a rate-limited call on a place we
    # cannot resolve.
    assert await provider().search_flights(query(origin="Atlantis")) == []


async def test_the_same_place_twice_is_not_a_journey():
    assert await provider().search_flights(query(origin="Goa")) == []


@respx.mock
async def test_no_data_for_a_route_is_an_empty_answer_not_an_error():
    respx.get(CALENDAR).mock(return_value=httpx.Response(200, json=calendar_body({})))
    assert await provider().search_flights(query()) == []


@respx.mock
async def test_rate_limiting_is_reported_as_retryable():
    respx.get(CALENDAR).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderError) as err:
        await provider().search_flights(query())
    assert err.value.retryable is True


@respx.mock
async def test_a_bad_token_is_not_retryable():
    # Trying again with the same wrong token wastes the budget and fails the
    # same way; this one needs a human.
    respx.get(CALENDAR).mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderError) as err:
        await provider().search_flights(query())
    assert err.value.retryable is False


@respx.mock
async def test_a_timeout_is_retryable():
    respx.get(CALENDAR).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(ProviderError) as err:
        await provider().search_flights(query())
    assert err.value.retryable is True


@respx.mock
async def test_malformed_json_does_not_escape_as_a_json_error():
    respx.get(CALENDAR).mock(return_value=httpx.Response(200, text="<html>nope"))
    with pytest.raises(ProviderError):
        await provider().search_flights(query())


@respx.mock
async def test_a_route_is_only_fetched_once_within_the_ttl():
    # The budget is 200 requests an hour for the whole deployment, and a
    # conversation asks about the same route repeatedly.
    route = respx.get(CALENDAR).mock(
        return_value=httpx.Response(
            200, json=calendar_body({"2026-12-25": row("2026-12-25")})
        )
    )
    p = provider()
    await p.search_flights(query())
    await p.search_flights(query(day=date(2026, 12, 26)))
    assert route.call_count == 1
