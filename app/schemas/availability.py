import datetime as dt

from pydantic import BaseModel, Field


class AvailabilityOut(BaseModel):
    doctor_id: int
    service_id: int
    date: dt.date
    slots: list[str] = Field(description="Bookable start times as HH:MM (clinic local time)")
