"""Turning what a person says into an airport code.

Worth testing properly: a wrong code does not error, it searches a real route
nobody asked about and answers confidently. Silent and wrong is the worst
failure this service has.
"""

from app.domains.trips.providers.iata import known_places, to_iata


def test_plain_city_names():
    assert to_iata("Delhi") == "DEL"
    assert to_iata("Kolkata") == "CCU"
    assert to_iata("Mumbai") == "BOM"


def test_the_old_names_people_still_use():
    # Renamed cities kept their codes, and half the country says the old name.
    assert to_iata("Bombay") == "BOM"
    assert to_iata("Calcutta") == "CCU"
    assert to_iata("Madras") == "MAA"
    assert to_iata("Bangalore") == "BLR"
    assert to_iata("Banaras") == to_iata("Varanasi") == "VNS"


def test_three_letter_city_names_are_not_codes():
    # The regression this test exists for: "Goa" is three letters, so a
    # code-first lookup turned it into the nonexistent GOA and every Goa
    # search came back empty. "Leh" broke identically.
    assert to_iata("Goa") == "GOI"
    assert to_iata("goa") == "GOI"
    assert to_iata("Leh") == "IXL"


def test_goa_resolves_to_the_airport_with_the_data():
    # Two airports serve Goa. Nearly all cached fare data sits under Dabolim,
    # so that is what the plain name means; Mopa is reachable by name.
    assert to_iata("Goa") == "GOI"
    assert to_iata("Mopa") == "GOX"


def test_codes_pass_through():
    assert to_iata("DEL") == "DEL"
    assert to_iata("bom") == "BOM"


def test_messy_speech():
    # This text arrives from speech recognition, not a form.
    assert to_iata("New  Delhi!") == "DEL"
    assert to_iata("goa, india") == "GOI"
    assert to_iata("fly to Varanasi please") == "VNS"


def test_unknown_places_return_none_rather_than_a_guess():
    assert to_iata("Atlantis") is None
    assert to_iata("") is None


def test_the_table_is_not_empty():
    # Guards against an edit that deletes the table and leaves every search
    # silently returning nothing.
    assert known_places() > 50
