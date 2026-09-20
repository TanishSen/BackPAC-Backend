"""Turning what a person says into what an airline API wants.

The agent hears "Kolkata", "Bombay", "Goa". Every flight API in existence wants
`CCU`, `BOM`, `GOI`. This is the translation, and it is not a detail: get it
wrong and the search silently returns nothing for a route that has flights.

**Why a table and not a lookup service.** Provider geocoding endpoints cost a
round trip per search, count against the rate limit, and disagree with each
other about which airport a city means. India has roughly forty airports that
matter and they do not move. A table is faster, free, testable, and wrong in
ways you can see and fix.

**The traps this table exists to avoid**, all of which are real:

- **Goa has two airports.** `GOI` (Dabolim) and `GOX` (Mopa, opened 2022).
  Most traffic and nearly all cached fare data still sits under GOI, so that
  is what "Goa" resolves to. Asking for GOX returns almost nothing.
- **Cities renamed, codes did not.** Bombay is `BOM`, Madras is `MAA`,
  Calcutta is `CCU`, Bangalore is `BLR`, Trivandrum is `TRV`. Both the old and
  the new name have to work, because people say both.
- **Delhi and Mumbai have several spellings** that arrive from speech
  recognition — "New Delhi", "Dilli". Matching is loose on purpose.
"""

from __future__ import annotations

import re
import unicodedata

#: City or airport name -> IATA code. Keys are normalised by `_key`, so the
#: spelling here only has to be one of the forms people use; matching handles
#: case, accents, punctuation and spacing.
#:
#: Ordered roughly by traffic. Add freely — a missing city is the single most
#: likely reason a real search comes back empty.
_CITIES: dict[str, str] = {
    # --- metros -----------------------------------------------------------
    "delhi": "DEL",
    "new delhi": "DEL",
    "ndls": "DEL",
    "mumbai": "BOM",
    "bombay": "BOM",
    "bengaluru": "BLR",
    "bangalore": "BLR",
    "hyderabad": "HYD",
    "chennai": "MAA",
    "madras": "MAA",
    "kolkata": "CCU",
    "calcutta": "CCU",
    "pune": "PNQ",
    "ahmedabad": "AMD",
    # --- tourism ----------------------------------------------------------
    # Goa: two airports, one answer. See the module docstring.
    "goa": "GOI",
    "dabolim": "GOI",
    "panaji": "GOI",
    "panjim": "GOI",
    "mopa": "GOX",
    "north goa": "GOX",
    "jaipur": "JAI",
    "udaipur": "UDR",
    "jodhpur": "JDH",
    "varanasi": "VNS",
    "banaras": "VNS",
    "benares": "VNS",
    "kochi": "COK",
    "cochin": "COK",
    "thiruvananthapuram": "TRV",
    "trivandrum": "TRV",
    "kozhikode": "CCJ",
    "calicut": "CCJ",
    "srinagar": "SXR",
    "leh": "IXL",
    "dehradun": "DED",
    "rishikesh": "DED",  # nearest airport
    "shimla": "SLV",
    "manali": "KUU",
    "kullu": "KUU",
    "dharamshala": "DHM",
    "mcleodganj": "DHM",
    "port blair": "IXZ",
    "andaman": "IXZ",
    "agra": "AGR",
    "amritsar": "ATQ",
    "chandigarh": "IXC",
    "lucknow": "LKO",
    "patna": "PAT",
    "bhubaneswar": "BBI",
    "puri": "BBI",  # nearest airport
    "guwahati": "GAU",
    "shillong": "GAU",  # nearest practical airport
    "gangtok": "PYG",
    "darjeeling": "IXB",
    "siliguri": "IXB",
    "bagdogra": "IXB",
    "indore": "IDR",
    "bhopal": "BHO",
    "nagpur": "NAG",
    "raipur": "RPR",
    "ranchi": "IXR",
    "coimbatore": "CJB",
    "madurai": "IXM",
    "tiruchirappalli": "TRZ",
    "trichy": "TRZ",
    "mangalore": "IXE",
    "hubli": "HBX",
    "vijayawada": "VGA",
    "visakhapatnam": "VTZ",
    "vizag": "VTZ",
    "tirupati": "TIR",
    "surat": "STV",
    "vadodara": "BDQ",
    "baroda": "BDQ",
    "rajkot": "RAJ",
    "jammu": "IXJ",
    "aurangabad": "IXU",
    "nashik": "ISK",
    "kanpur": "KNU",
    "gorakhpur": "GOP",
    "jabalpur": "JLR",
    "gwalior": "GWL",
    "jaisalmer": "JSA",
    "bikaner": "BKB",
    "imphal": "IMF",
    "agartala": "IXA",
    "aizawl": "AJL",
    "dibrugarh": "DIB",
    "jorhat": "JRH",
    "silchar": "IXS",
    # --- a few international ones people ask for --------------------------
    "dubai": "DXB",
    "abu dhabi": "AUH",
    "singapore": "SIN",
    "bangkok": "BKK",
    "kathmandu": "KTM",
    "colombo": "CMB",
    "male": "MLE",
    "maldives": "MLE",
    "london": "LON",
    "new york": "NYC",
}


def _key(text: str) -> str:
    """Normalise a spoken place name for lookup.

    Lowercase, strip accents, drop anything that is not a letter or a space,
    collapse whitespace. "New  Delhi!" and "new delhi" become the same key,
    which matters because this text arrives from speech recognition.
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^a-z\s]", " ", folded.lower())
    return re.sub(r"\s+", " ", folded).strip()


def to_iata(place: str) -> str | None:
    """The IATA code for a city or airport, or None if we do not know it.

    None rather than a guess. A wrong code searches a real route that nobody
    asked about and returns confident, useless answers — far worse than the
    caller saying it did not recognise the city.
    """
    if not place:
        return None

    # The table first, always.
    #
    # The obvious order — treat any three letters as a code, then look up
    # names — is wrong, and quietly so: "Goa" is three letters, so it became
    # the nonexistent code GOA and every Goa search returned nothing. "Leh"
    # would have done the same. A known name is never a code.
    key = _key(place)
    if key in _CITIES:
        return _CITIES[key]

    # Not a name we know, so a bare three-letter token is probably already a
    # code: "DEL", "bom".
    bare = place.strip().upper()
    if len(bare) == 3 and bare.isalpha():
        return bare

    # "Goa, India" / "Delhi airport" / "to Mumbai" — try the informative part
    # rather than giving up on a preposition or a country.
    noise = {"india", "airport", "city", "to", "from", "in", "the"}
    words = [w for w in key.split() if w not in noise]
    if words:
        trimmed = " ".join(words)
        if trimmed in _CITIES:
            return _CITIES[trimmed]
        # Single distinctive word inside a longer phrase: "fly to goa please".
        for w in words:
            if w in _CITIES:
                return _CITIES[w]
    return None


def known_places() -> int:
    """How many names resolve. Used by a test to catch an emptied table."""
    return len(_CITIES)
