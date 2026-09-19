import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import appointments, availability, doctors, services, whatsapp
from app.config import get_settings
from app.database import database
from app.database.seed import seed_database
from app.services import whatsapp_service
from app.services.errors import (
    BookingError,
    InvalidRequestError,
    InvalidStateError,
    NotFoundError,
    SlotUnavailableError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.init_db()
    with database.SessionLocal() as db:
        if seed_database(db):
            logger.info("Database was empty - seeded the Astra Dental Clinic demo data.")

    settings = get_settings()
    if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
        logger.warning("WhatsApp credentials are not set: outgoing messages will only be logged (dry-run).")
    if not settings.whatsapp_app_secret:
        logger.warning("WHATSAPP_APP_SECRET is not set: webhook POSTs will be refused unless WHATSAPP_ALLOW_UNSIGNED=true.")
    yield
    whatsapp_service.get_whatsapp_client().close()


app = FastAPI(
    title="Astra Dental Clinic - WhatsApp Booking",
    description=(
        "Deterministic appointment booking (no AI). The same booking engine backs this REST API "
        "and the WhatsApp conversation flow. These REST endpoints are unauthenticated demo endpoints "
        "- put them behind authentication before exposing them publicly."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(services.router)
app.include_router(doctors.router)
app.include_router(availability.router)
app.include_router(appointments.router)
app.include_router(whatsapp.router)


_STATUS_BY_ERROR: list[tuple[type[BookingError], int]] = [
    (NotFoundError, 404),
    (SlotUnavailableError, 409),
    (InvalidStateError, 409),
    (InvalidRequestError, 422),
]


@app.exception_handler(BookingError)
async def booking_error_handler(_request: Request, exc: BookingError) -> JSONResponse:
    status_code = next((code for error_type, code in _STATUS_BY_ERROR if isinstance(exc, error_type)), 400)
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
