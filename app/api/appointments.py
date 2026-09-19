from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentOut,
    CancelRequest,
    RescheduleRequest,
)
from app.services import booking_service

router = APIRouter(prefix="/appointments", tags=["Appointments"])


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
def create_appointment(payload: AppointmentCreate, db: Session = Depends(get_db)):
    """Book an appointment. Availability is re-checked at this moment; 409 if the slot was just taken."""
    appointment = booking_service.create_appointment(
        db,
        patient_name=payload.patient_name,
        phone=payload.phone,
        doctor_id=payload.doctor_id,
        service_id=payload.service_id,
        start=payload.start_datetime,
    )
    return AppointmentOut.from_appointment(appointment)


@router.get("", response_model=list[AppointmentOut])
def list_appointments(
    phone: str = Query(description="Patient phone number (any common format; normalized server-side)"),
    upcoming_only: bool = Query(default=True, description="Only confirmed, not-yet-started appointments"),
    db: Session = Depends(get_db),
):
    """A patient's appointments, looked up by normalized phone number."""
    appointments = booking_service.get_patient_appointments(db, phone=phone, upcoming_only=upcoming_only)
    return [AppointmentOut.from_appointment(a) for a in appointments]


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel_appointment(appointment_id: int, payload: CancelRequest, db: Session = Depends(get_db)):
    """Cancel an appointment (status becomes CANCELLED; the slot is free again immediately)."""
    appointment = booking_service.cancel_appointment(db, appointment_id, phone=payload.phone)
    return AppointmentOut.from_appointment(appointment)


@router.post("/{appointment_id}/reschedule", response_model=AppointmentOut)
def reschedule_appointment(appointment_id: int, payload: RescheduleRequest, db: Session = Depends(get_db)):
    """Move an appointment to a new time with the same doctor. The old slot is kept unless the new one is free."""
    appointment = booking_service.reschedule_appointment(
        db, appointment_id, phone=payload.phone, new_start=payload.new_start_datetime
    )
    return AppointmentOut.from_appointment(appointment)
