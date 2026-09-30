"""Imports every model module, so `Base.metadata` is complete.

Alembic's autogenerate and anything else that reflects on the schema needs all
the tables registered, and a table is only registered once the module defining
it has been imported. Import this one module and you have them all; add a line
here whenever a domain gains its first table.
"""

from app.domains.account import models as account_models  # noqa: F401
from app.domains.sessions import models as sessions_models  # noqa: F401
from app.domains.trips import models as trips_models  # noqa: F401

__all__ = ["account_models", "sessions_models", "trips_models"]
