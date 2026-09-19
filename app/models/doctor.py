from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base
from app.utils.datetime_utils import now_local

# Many-to-many link between doctors and the services they perform (DoctorService).
doctor_services = Table(
    "doctor_services",
    Base.metadata,
    Column("doctor_id", ForeignKey("doctors.id", ondelete="CASCADE"), primary_key=True),
    Column("service_id", ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
)


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"))
    name: Mapped[str] = mapped_column(String(120))
    specialization: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    services: Mapped[list["Service"]] = relationship(
        secondary=doctor_services, back_populates="doctors", order_by="Service.id"
    )
    schedules: Mapped[list["DoctorSchedule"]] = relationship(
        back_populates="doctor",
        cascade="all, delete-orphan",
        order_by="DoctorSchedule.day_of_week, DoctorSchedule.start_time",
    )
