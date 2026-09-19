"""Domain errors raised by the service layer.

The REST API maps these to HTTP status codes and the WhatsApp layer maps them to
patient-friendly messages, so business logic never needs to know about either channel.
"""


class BookingError(Exception):
    """Base class for all expected business-rule failures."""

    default_message = "The request could not be completed."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)


class NotFoundError(BookingError):
    """Doctor, service or appointment does not exist (or is not visible to this patient)."""

    default_message = "Not found."


class InvalidRequestError(BookingError):
    """The input is syntactically fine but not acceptable (bad phone, wrong doctor/service pair...)."""

    default_message = "Invalid request."


class SlotUnavailableError(BookingError):
    """The requested time is not (or no longer) available."""

    default_message = "This appointment time is no longer available."


class InvalidStateError(BookingError):
    """The appointment's current status does not allow this operation."""

    default_message = "This appointment cannot be changed."
