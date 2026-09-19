import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.availability import AvailabilityOut
from app.services import availability_service
from app.utils.datetime_utils import format_slot_id

router = APIRouter(tags=["Availability"])


@router.get("/availability", response_model=AvailabilityOut)
def get_availability(
    doctor_id: int,
    service_id: int,
    day: dt.date = Query(alias="date", description="YYYY-MM-DD", examples=["2026-09-21"]),
    db: Session = Depends(get_db),
):
    """Slots a doctor can currently offer for a service on a date (empty if closed or fully booked)."""
    slots = availability_service.get_available_slots(db, doctor_id=doctor_id, service_id=service_id, day=day)
    return AvailabilityOut(
        doctor_id=doctor_id,
        service_id=service_id,
        date=day,
        slots=[format_slot_id(slot) for slot in slots],
    )
