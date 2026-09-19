"""Seed the database with the fictional Astra Dental Clinic.

Usage (from the project root):

    python -m app.database.seed            # create tables + seed if the DB is empty
    python -m app.database.seed --reset    # drop everything, recreate and re-seed
"""

import argparse
from datetime import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import database
from app.models import Clinic, Doctor, DoctorSchedule, Service

# Monday-Saturday, two blocks per day. Sunday (6) has no rows, i.e. the doctor is off.
WORKING_DAYS = range(0, 6)
WORKING_BLOCKS = [(time(9, 0), time(13, 0)), (time(14, 0), time(18, 0))]

SERVICES = [
    # id, name, duration (minutes)
    (1, "General Consultation", 30),
    (2, "Teeth Cleaning", 30),
    (3, "Root Canal", 60),
    (4, "Orthodontic Consultation", 30),
]

DOCTORS = [
    # name, specialization, service ids
    ("Dr. Ananya Sharma", "General Dentist", [1, 2, 3]),
    ("Dr. Rohan Mehta", "Orthodontist", [1, 4]),
    ("Dr. Priya Kapoor", "Dental Surgeon", [1, 3]),
]


def seed_database(db: Session) -> bool:
    """Insert the demo data. Returns False (and does nothing) if a clinic already exists."""
    if db.scalar(select(Clinic.id).limit(1)) is not None:
        return False

    clinic = Clinic(
        name="Astra Dental Clinic",
        phone="+918000000000",
        address="42 Lotus Street, Indiranagar, Bengaluru 560038",
    )
    db.add(clinic)
    db.flush()

    services = {
        service_id: Service(id=service_id, clinic_id=clinic.id, name=name, duration_minutes=minutes)
        for service_id, name, minutes in SERVICES
    }
    db.add_all(services.values())

    for name, specialization, service_ids in DOCTORS:
        doctor = Doctor(
            clinic_id=clinic.id,
            name=name,
            specialization=specialization,
            services=[services[sid] for sid in service_ids],
            schedules=[
                DoctorSchedule(day_of_week=day, start_time=start, end_time=end)
                for day in WORKING_DAYS
                for start, end in WORKING_BLOCKS
            ],
        )
        db.add(doctor)

    db.commit()
    return True


def print_summary(db: Session) -> None:
    clinic = db.scalar(select(Clinic))
    print(f"Clinic: {clinic.name} ({clinic.address})")
    print("\nServices:")
    for service in db.scalars(select(Service).order_by(Service.id)):
        print(f"  {service.id}. {service.name} ({service.duration_minutes} min)")
    print("\nDoctors:")
    for doctor in db.scalars(select(Doctor).order_by(Doctor.id)):
        blocks = db.scalar(
            select(func.count()).select_from(DoctorSchedule).where(DoctorSchedule.doctor_id == doctor.id)
        )
        offered = ", ".join(service.name for service in doctor.services)
        print(f"  {doctor.id}. {doctor.name} - {doctor.specialization} [{blocks} schedule blocks]")
        print(f"     services: {offered}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and seed the clinic database.")
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    args = parser.parse_args()

    import app.models  # noqa: F401

    if args.reset:
        database.Base.metadata.drop_all(database.engine)
        print("Dropped all tables.")
    database.init_db()

    with database.SessionLocal() as db:
        seeded = seed_database(db)
        print("Seeded demo data." if seeded else "Database already contains data; nothing to seed.")
        print()
        print_summary(db)


if __name__ == "__main__":
    main()
