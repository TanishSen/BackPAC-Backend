"""Domain exceptions.

Raise these from the service layer instead of `HTTPException`. The service
stays free of HTTP concepts (it might one day be called from a worker, not a
route), and `errors_handlers.py` turns each of these into the right status
code in one place.
"""


class AppError(Exception):
    """Base class. `status_code` is what the client will receive."""

    status_code = 500
    message = "Something went wrong."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


class NotFoundError(AppError):
    status_code = 404
    message = "Not found."


class BadRequestError(AppError):
    status_code = 400
    message = "Bad request."


class PaymentRequiredError(AppError):
    """The free plan's allowance is used up. The app answers with the upgrade
    screen, so the message is written to be shown there."""

    status_code = 402
    message = "You've used this month's free trip plans."


class UpstreamError(AppError):
    """A service we depend on (LiveKit, the agent) failed or is unreachable."""

    status_code = 502
    message = "An upstream service failed."


class ConfigurationError(AppError):
    """The server is missing configuration it needs to do this — our fault, not
    the caller's, but worth a clear message rather than a stack trace."""

    status_code = 503
    message = "The server is not configured for this yet."
