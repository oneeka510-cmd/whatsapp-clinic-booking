"""Glue between the Meta WhatsApp Cloud API and the conversation engine.

Inbound:  verify the webhook signature -> parse -> de-duplicate -> ConversationService.
Outbound: channel-neutral messages -> Cloud API JSON -> POST to the Graph API.

There is deliberately no booking logic here.
"""

import hashlib
import hmac
import json
import logging
import re
import threading
from functools import lru_cache

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import ProcessedMessage
from app.schemas.messages import (
    MAX_BUTTONS,
    MAX_LIST_ROWS,
    ButtonMessage,
    InboundMessage,
    ListMessage,
    OutboundMessage,
    TextMessage,
)
from app.schemas.whatsapp import WAWebhookPayload
from app.services.conversation_service import ConversationService
from app.utils.phone_utils import mask_phone, normalize_phone

logger = logging.getLogger(__name__)

# Serialises conversation handling so two quick messages from one patient can't race on their session.
_conversation_lock = threading.Lock()

MAX_TEXT_LENGTH = 1000  # longer patient messages are truncated before processing

FALLBACK_TEXT = "Sorry, something went wrong on our side. Please send *menu* to start again."


# ---------------------------------------------------------------------- security


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Check Meta's `X-Hub-Signature-256: sha256=<hex HMAC of the raw body>` header."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def verify_token_matches(configured: str, supplied: str | None) -> bool:
    """Constant-time comparison for the webhook verification handshake (empty never matches)."""
    if not configured or not supplied:
        return False
    return hmac.compare_digest(configured.encode(), supplied.encode())


# ---------------------------------------------------------------------- inbound


def extract_inbound_messages(payload: WAWebhookPayload) -> list[InboundMessage]:
    """Flatten a webhook payload into the messages patients actually sent."""
    inbound: list[InboundMessage] = []
    for entry in payload.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            for message in change.value.messages:
                try:
                    phone = normalize_phone("+" + re.sub(r"\D", "", message.from_))
                except ValueError:
                    logger.warning("Ignoring message with an invalid sender number")
                    continue

                common = {"phone": phone, "message_id": message.id}
                reply = None
                if message.interactive is not None:
                    reply = message.interactive.button_reply or message.interactive.list_reply

                if message.type == "text" and message.text is not None:
                    inbound.append(InboundMessage(kind="text", text=message.text.body[:MAX_TEXT_LENGTH], **common))
                elif message.type == "interactive" and reply is not None:
                    inbound.append(InboundMessage(kind="reply", reply_id=reply.id[:256], **common))
                else:
                    inbound.append(InboundMessage(kind="unsupported", **common))
    return inbound


def _claim_message(db: Session, message_id: str) -> bool:
    """Record a WhatsApp message id; False if it was already processed (Meta retries deliveries)."""
    db.add(ProcessedMessage(id=message_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def process_webhook_payload(
    payload: WAWebhookPayload,
    session_factory: sessionmaker[Session],
    client: "WhatsAppClient",
) -> None:
    """Handle every message in a webhook payload. Runs in the background after Meta got its 200."""
    for message in extract_inbound_messages(payload):
        try:
            with session_factory() as db:
                with _conversation_lock:
                    if not _claim_message(db, message.message_id):
                        logger.info("Skipping duplicate delivery of a message")
                        continue
                    outbound = ConversationService(db).handle(message)
            client.send_all(message.phone, outbound)
        except Exception:  # never let one bad message break the rest or surface to Meta
            logger.exception("Failed to process a message from %s", mask_phone(message.phone))
            try:
                client.send_all(message.phone, [TextMessage(FALLBACK_TEXT)])
            except Exception:
                logger.exception("Could not send the fallback reply either")


# ---------------------------------------------------------------------- outbound


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_payload(to: str, message: OutboundMessage) -> dict:
    """Translate a channel-neutral message into a Cloud API `messages` request body.

    Titles/bodies are clipped to Meta's limits as a safety net so an unexpectedly long name can
    never turn into a rejected message.
    """
    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
    }

    if isinstance(message, TextMessage):
        payload.update(type="text", text={"preview_url": False, "body": _clip(message.body, 4096)})
        return payload

    if isinstance(message, ButtonMessage):
        interactive: dict = {
            "type": "button",
            "body": {"text": _clip(message.body, 1024)},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.id[:256], "title": _clip(b.title, 20)}}
                    for b in message.buttons[:MAX_BUTTONS]
                ]
            },
        }
    elif isinstance(message, ListMessage):
        rows = []
        for row in message.rows[:MAX_LIST_ROWS]:
            item = {"id": row.id[:200], "title": _clip(row.title, 24)}
            if row.description:
                item["description"] = _clip(row.description, 72)
            rows.append(item)
        interactive = {
            "type": "list",
            "body": {"text": _clip(message.body, 1024)},
            "action": {
                "button": _clip(message.button_label, 20),
                "sections": [{"title": _clip(message.section_title, 24), "rows": rows}],
            },
        }
    else:
        raise TypeError(f"Unsupported message type: {type(message).__name__}")

    if message.footer:
        interactive["footer"] = {"text": _clip(message.footer, 60)}
    payload.update(type="interactive", interactive=interactive)
    return payload


class WhatsAppClient:
    """Sends messages through the Cloud API.

    If no access token / phone number id is configured it runs in dry-run mode and only logs
    what it would send - handy for trying the flow locally before Meta is set up.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._http = httpx.Client(timeout=10.0)

    @property
    def configured(self) -> bool:
        return bool(self._settings.whatsapp_access_token and self._settings.whatsapp_phone_number_id)

    @property
    def _url(self) -> str:
        s = self._settings
        return f"https://graph.facebook.com/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}/messages"

    def send(self, to: str, message: OutboundMessage) -> bool:
        payload = build_payload(to, message)
        if not self.configured:
            logger.info("[dry-run] WhatsApp not configured; would send to %s: %s", mask_phone(to), json.dumps(payload, ensure_ascii=False))
            return True

        try:
            response = self._http.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._settings.whatsapp_access_token}"},
            )
        except httpx.HTTPError as exc:
            logger.error("WhatsApp send to %s failed: %s", mask_phone(to), exc.__class__.__name__)
            return False

        if response.status_code >= 400:
            # Meta's error body describes what was wrong; it never contains our token.
            logger.error("WhatsApp send to %s rejected (%s): %s", mask_phone(to), response.status_code, response.text[:500])
            return False
        return True

    def send_all(self, to: str, messages: list[OutboundMessage]) -> None:
        for message in messages:  # sequential, so patients see them in order
            self.send(to, message)

    def close(self) -> None:
        self._http.close()


@lru_cache
def get_whatsapp_client() -> WhatsAppClient:
    return WhatsAppClient(get_settings())
