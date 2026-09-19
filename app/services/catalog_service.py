"""Read-only lookups for the clinic, its services and its doctors."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Clinic, Doctor, Service
from app.services.errors import InvalidRequestError, NotFoundError


def get_clinic(db: Session) -> Clinic | None:
    """The demo runs a single clinic."""
    return db.scalar(select(Clinic).order_by(Clinic.id).limit(1))


def list_services(db: Session) -> list[Service]:
    return list(db.scalars(select(Service).where(Service.active.is_(True)).order_by(Service.id)))


def list_doctors(db: Session, service_id: int | None = None) -> list[Doctor]:
    """Active doctors, optionally only those who perform `service_id`."""
    query = select(Doctor).where(Doctor.active.is_(True)).order_by(Doctor.id)
    if service_id is not None:
        query = query.where(Doctor.services.any(Service.id == service_id))
    return list(db.scalars(query))


def get_active_service(db: Session, service_id: int) -> Service:
    service = db.get(Service, service_id)
    if service is None or not service.active:
        raise NotFoundError("Service not found.")
    return service


def get_active_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None or not doctor.active:
        raise NotFoundError("Doctor not found.")
    return doctor


def ensure_doctor_offers_service(doctor: Doctor, service: Service) -> None:
    if service.id not in {s.id for s in doctor.services}:
        raise InvalidRequestError(f"{doctor.name} does not offer {service.name}.")
