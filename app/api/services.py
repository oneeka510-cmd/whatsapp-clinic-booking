from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.service import ServiceOut
from app.services import catalog_service

router = APIRouter(tags=["Services"])


@router.get("/services", response_model=list[ServiceOut])
def list_services(db: Session = Depends(get_db)):
    """All active services with their durations."""
    return catalog_service.list_services(db)
