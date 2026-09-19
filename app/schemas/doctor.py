from pydantic import BaseModel, ConfigDict

from app.schemas.service import ServiceOut


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    specialization: str
    services: list[ServiceOut]
