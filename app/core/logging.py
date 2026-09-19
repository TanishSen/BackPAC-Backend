"""One place to configure logging for the whole service.

Call `configure_logging()` once at startup (main.py does). After that, any
module can `import logging; logger = logging.getLogger(__name__)` and get
consistent, timestamped output.
"""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
