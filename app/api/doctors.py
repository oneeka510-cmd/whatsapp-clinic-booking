from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.doctor import DoctorOut
from app.services import catalog_service

router = APIRouter(tags=["Doctors"])


@router.get("/doctors", response_model=list[DoctorOut])
def list_doctors(
    service_id: int | None = Query(default=None, description="Only doctors who perform this service"),
    db: Session = Depends(get_db),
):
    """Active doctors, optionally filtered to those who perform a given service."""
    return catalog_service.list_doctors(db, service_id=service_id)
