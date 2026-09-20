"""Where trip data actually comes from.

One module per provider, all behind the protocols in `base.py`, so the service
layer never learns which company is on the other end. Swapping Travelpayouts
for a licensed GDS later is a new file here and one line in `deps.py`.
"""

from app.domains.trips.providers.base import FlightProvider

__all__ = ["FlightProvider"]
