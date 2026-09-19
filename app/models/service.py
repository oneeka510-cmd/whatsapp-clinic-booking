from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (CheckConstraint("duration_minutes > 0", name="ck_services_duration"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"))
    name: Mapped[str] = mapped_column(String(120))
    duration_minutes: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)

    doctors: Mapped[list["Doctor"]] = relationship(
        secondary="doctor_services", back_populates="services", order_by="Doctor.id"
    )
