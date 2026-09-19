import datetime as dt
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.models import Appointment, AppointmentStatus
from app.utils.datetime_utils import to_clinic_naive


def _to_local_naive(value: dt.datetime) -> dt.datetime:
    """Datetimes with an offset are converted to clinic-local time; naive ones are taken as local."""
    return to_clinic_naive(value)


ClinicDatetime = Annotated[dt.datetime, AfterValidator(_to_local_naive)]


class AppointmentCreate(BaseModel):
    patient_name: str = Field(min_length=2, max_length=60, examples=["Rahul Sharma"])
    phone: str = Field(min_length=5, max_length=32, examples=["+919876543210"])
    doctor_id: int = Field(examples=[1])
    service_id: int = Field(examples=[1])
    start_datetime: ClinicDatetime = Field(examples=["2026-09-21T17:30:00"])


class CancelRequest(BaseModel):
    phone: str = Field(
        min_length=5,
        max_length=32,
        description="Phone number the appointment was booked with (proves ownership in this demo API).",
        examples=["+919876543210"],
    )


class RescheduleRequest(CancelRequest):
    new_start_datetime: ClinicDatetime = Field(examples=["2026-09-22T10:00:00"])


class DoctorRef(BaseModel):
    id: int
    name: str
    specialization: str


class ServiceRef(BaseModel):
    id: int
    name: str
    duration_minutes: int


class AppointmentOut(BaseModel):
    id: int
    booking_reference: str
    patient_name: str
    phone: str
    doctor: DoctorRef
    service: ServiceRef
    start_datetime: dt.datetime
    end_datetime: dt.datetime
    status: AppointmentStatus

    @classmethod
    def from_appointment(cls, appointment: Appointment) -> "AppointmentOut":
        return cls(
            id=appointment.id,
            booking_reference=appointment.booking_reference,
            patient_name=appointment.patient.name,
            phone=appointment.patient.phone,
            doctor=DoctorRef(
                id=appointment.doctor.id,
                name=appointment.doctor.name,
                specialization=appointment.doctor.specialization,
            ),
            service=ServiceRef(
                id=appointment.service.id,
                name=appointment.service.name,
                duration_minutes=appointment.service.duration_minutes,
            ),
            start_datetime=appointment.start_datetime,
            end_datetime=appointment.end_datetime,
            status=appointment.status,
        )
