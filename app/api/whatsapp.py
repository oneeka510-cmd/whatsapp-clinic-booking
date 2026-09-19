"""Thin WhatsApp webhook layer: authenticate, parse, hand off. No business logic lives here."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database import database
from app.schemas.whatsapp import WAWebhookPayload
from app.services import whatsapp_service
from app.services.whatsapp_service import WhatsAppClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["WhatsApp"])


def get_session_factory() -> sessionmaker[Session]:
    return database.SessionLocal


def get_client() -> WhatsAppClient:
    return whatsapp_service.get_whatsapp_client()


@router.get("/whatsapp", response_class=PlainTextResponse)
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
):
    """Meta's one-time webhook verification handshake: echo `hub.challenge` if the token matches."""
    if (
        mode == "subscribe"
        and challenge is not None
        and whatsapp_service.verify_token_matches(settings.whatsapp_verify_token, verify_token)
    ):
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/whatsapp")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    client: WhatsAppClient = Depends(get_client),
):
    """Incoming WhatsApp events. Replies straight away with 200 and processes in the background."""
    raw_body = await request.body()

    if settings.whatsapp_app_secret:
        signature = request.headers.get("X-Hub-Signature-256")
        if not whatsapp_service.verify_signature(settings.whatsapp_app_secret, raw_body, signature):
            logger.warning("Rejected webhook with a missing or invalid signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
    elif not settings.whatsapp_allow_unsigned:
        logger.error("WHATSAPP_APP_SECRET is not set, so webhook requests cannot be verified; refusing them")
        raise HTTPException(status_code=403, detail="Webhook signature verification is not configured")

    try:
        payload = WAWebhookPayload.model_validate_json(raw_body)
    except ValidationError as exc:
        if any(error["type"] == "json_invalid" for error in exc.errors()):
            return JSONResponse(status_code=400, content={"detail": "Body is not valid JSON"})
        logger.warning("Ignoring an unrecognised webhook payload")
        return {"status": "ignored"}

    background_tasks.add_task(whatsapp_service.process_webhook_payload, payload, session_factory, client)
    return {"status": "received"}
