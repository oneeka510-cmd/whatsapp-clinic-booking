"""Shared fixtures.

Environment variables are set *before* any `app` import so the tests never touch a
developer's real `clinic.db` or use real WhatsApp credentials from `.env`.
"""

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["WHATSAPP_ACCESS_TOKEN"] = ""
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = ""
os.environ["WHATSAPP_VERIFY_TOKEN"] = "test-verify-token"
os.environ["WHATSAPP_APP_SECRET"] = "test-app-secret"
os.environ["WHATSAPP_ALLOW_UNSIGNED"] = "false"
os.environ["CLINIC_TIMEZONE"] = "Asia/Kolkata"

from datetime import date, datetime, time, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.models  # noqa: E402,F401
from app.database.database import Base, make_engine  # noqa: E402
from app.database.seed import seed_database  # noqa: E402
from app.models import Appointment, AppointmentStatus, Patient  # noqa: E402

# Saturday 19 September 2026, 10:00 (clinic time). Sunday the 20th is closed; Monday the 21st is
# the first full working day and is used by most tests.
NOW = datetime(2026, 9, 19, 10, 0)
SATURDAY = date(2026, 9, 19)
SUNDAY = date(2026, 9, 20)
MONDAY = date(2026, 9, 21)

# Seeded ids
GENERAL_CONSULTATION, TEETH_CLEANING, ROOT_CANAL, ORTHO_CONSULTATION = 1, 2, 3, 4
DR_SHARMA, DR_MEHTA, DR_KAPOOR = 1, 2, 3

PHONE_A = "+919876543210"
PHONE_B = "+919123456780"


def at(day: date, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime.combine(day, time(hour, minute))


def hhmm(slots: list[datetime]) -> list[str]:
    return [slot.strftime("%H:%M") for slot in slots]


@pytest.fixture
def engine():
    eng = make_engine("sqlite://")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        seed_database(session)
        yield session


def insert_appointment(
    db: Session,
    *,
    doctor_id: int = DR_SHARMA,
    start: datetime,
    minutes: int = 30,
    service_id: int = GENERAL_CONSULTATION,
    status: AppointmentStatus = AppointmentStatus.CONFIRMED,
    phone: str = PHONE_B,
) -> Appointment:
    """Insert an appointment straight through the ORM (bypasses the booking service)."""
    patient = db.query(Patient).filter_by(phone=phone).one_or_none()
    if patient is None:
        patient = Patient(name="Existing Patient", phone=phone)
        db.add(patient)
        db.flush()
    appointment = Appointment(
        booking_reference=f"TMP-{db.query(Appointment).count() + 1}",
        patient_id=patient.id,
        doctor_id=doctor_id,
        service_id=service_id,
        start_datetime=start,
        end_datetime=start + timedelta(minutes=minutes),
        status=status,
    )
    db.add(appointment)
    db.commit()
    return appointment
