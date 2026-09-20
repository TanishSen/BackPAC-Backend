"""What the service does when a provider misbehaves.

The service's one real job beyond delegation is containment: a flight provider
having a bad day must not end a phone call that is currently happening.
"""

from datetime import date

from app.domains.trips.providers.base import ProviderError
from app.domains.trips.schemas import TransitOption, TransitSearch
from app.domains.trips.service import TripService

QUERY = TransitSearch(
    origin="Delhi", destination="Mumbai", departDate=date(2026, 12, 25)
)


class _Provider:
    name = "fake"

    def __init__(self, *, result=None, raises=None):
        self._result = result or []
        self._raises = raises
        self.calls = 0

    async def search_flights(self, q):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._result


async def test_results_pass_straight_through():
    option = TransitOption(
        provider="IndiGo",
        name="6E-634",
        depart="2026-12-25T10:30:00+05:30",
        arrive="",
        durationMinutes=175,
        priceInr=21229,
    )
    out = await TripService(_Provider(result=[option])).search_flights(QUERY)
    assert out == [option]


async def test_a_provider_failure_is_an_empty_answer_not_an_exception():
    # The caller is an agent mid-conversation. "I could not find flights just
    # now" is a recoverable turn; a 502 ends the call.
    svc = TripService(_Provider(raises=ProviderError("down", retryable=True)))
    assert await svc.search_flights(QUERY) == []


async def test_even_an_unexpected_crash_is_contained():
    # A provider that raises something nobody anticipated — a parsing bug, a
    # bad assumption — still must not take the call down with it.
    svc = TripService(_Provider(raises=ZeroDivisionError("oops")))
    assert await svc.search_flights(QUERY) == []


async def test_no_flights_found_is_reported_as_no_flights():
    svc = TripService(_Provider(result=[]))
    assert await svc.search_flights(QUERY) == []


async def test_the_service_does_not_retry_on_its_own():
    # Retrying inside a voice turn spends a rate-limited budget and makes the
    # user wait twice. If a retry belongs anywhere it is above this.
    p = _Provider(raises=ProviderError("down", retryable=True))
    await TripService(p).search_flights(QUERY)
    assert p.calls == 1
