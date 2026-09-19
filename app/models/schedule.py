from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class DoctorSchedule(Base):
    """One working block for a doctor on a weekday. Several blocks per day are allowed."""

    __tablename__ = "doctor_schedules"
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_schedule_day"),
        CheckConstraint("start_time < end_time", name="ck_schedule_times"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="CASCADE"), index=True)
    day_of_week: Mapped[int]  # 0 = Monday ... 6 = Sunday (date.weekday())
    start_time: Mapped[time]
    end_time: Mapped[time]

    doctor: Mapped["Doctor"] = relationship(back_populates="schedules")
