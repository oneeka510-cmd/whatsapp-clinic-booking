"""The WhatsApp webhook layer: verification, signatures, parsing, payload building, delivery.

Payloads mirror the shapes documented for the Meta WhatsApp Cloud API.
"""

import hashlib
import hmac
import json
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.whatsapp import get_client, get_session_factory
from app.config import Settings, get_settings
from app.main import app
from app.models import Appointment, ConversationSession, ProcessedMessage
from app.schemas.messages import Button, ButtonMessage, ListMessage, ListRow, TextMessage
from app.schemas.whatsapp import WAWebhookPayload
from app.services import whatsapp_service
from tests.conftest import PHONE_A, PHONE_B

SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
WA_ID_A = "919876543210"  # what Meta sends: digits only, with country code
_ids = count(1)


# --- payload builders (Meta's webhook format) -----------------------------------------


def envelope(*messages: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550001111", "phone_number_id": "PNID"},
                            "contacts": [{"profile": {"name": "Rahul"}, "wa_id": WA_ID_A}],
                            "messages": list(messages),
                        },
                    }
                ],
            }
        ],
    }


def text_message(text: str, sender: str = WA_ID_A, message_id: str | None = None) -> dict:
    return {
        "from": sender,
        "id": message_id or f"wamid.T{next(_ids)}",
        "timestamp": "1790000000",
        "type": "text",
        "text": {"body": text},
    }


def button_reply(reply_id: str, sender: str = WA_ID_A, message_id: str | None = None) -> dict:
    return {
        "from": sender,
        "id": message_id or f"wamid.B{next(_ids)}",
        "timestamp": "1790000000",
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": reply_id, "title": "ignored"}},
    }


def list_reply(reply_id: str, sender: str = WA_ID_A, message_id: str | None = None) -> dict:
    return {
        "from": sender,
        "id": message_id or f"wamid.L{next(_ids)}",
        "timestamp": "1790000000",
        "type": "interactive",
        "interactive": {"type": "list_reply", "list_reply": {"id": reply_id, "title": "ignored", "description": "x"}},
    }


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeClient:
    """Stands in for WhatsAppClient and records what would have been sent."""

    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    def send_all(self, to, messages):
        self.sent.extend((to, m) for m in messages)

    @property
    def last(self):
        return self.sent[-1][1]


@pytest.fixture
def wa(engine, db):
    """A TestClient wired to the in-memory database and a recording fake WhatsApp client."""
    fake = FakeClient()
    factory = sessionmaker(bind=engine)
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_client] = lambda: fake

    class Harness:
        client = TestClient(app)
        outbox = fake
        session_factory = factory

        def post(self, payload, *, signature=True, body=None):
            raw = body if body is not None else json.dumps(payload).encode()
            headers = {"Content-Type": "application/json"}
            if signature is True:
                headers["X-Hub-Signature-256"] = sign(raw)
            elif signature:
                headers["X-Hub-Signature-256"] = signature
            return self.client.post("/webhooks/whatsapp", content=raw, headers=headers)

        def send(self, message: dict):
            response = self.post(envelope(message))
            assert response.status_code == 200
            return self.outbox.last

    yield Harness()
    app.dependency_overrides.clear()


# --- verification handshake ------------------------------------------------------------


def test_webhook_verification_echoes_the_challenge(wa):
    response = wa.client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444"},
    )
    assert response.status_code == 200
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
        {},
    ],
)
def test_webhook_verification_rejects_bad_requests(wa, params):
    assert wa.client.get("/webhooks/whatsapp", params=params).status_code == 403


def test_empty_configured_verify_token_never_matches(wa):
    app.dependency_overrides[get_settings] = lambda: Settings(whatsapp_verify_token="")
    response = wa.client.get(
        "/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
    )
    assert response.status_code == 403


# --- signatures ------------------------------------------------------------------------


def test_signature_helper():
    body = b'{"a": 1}'
    assert whatsapp_service.verify_signature(SECRET, body, sign(body))
    assert not whatsapp_service.verify_signature(SECRET, body, sign(body, "other-secret"))
    assert not whatsapp_service.verify_signature(SECRET, body + b" ", sign(body))
    assert not whatsapp_service.verify_signature(SECRET, body, None)
    assert not whatsapp_service.verify_signature(SECRET, body, "sha256=")
    assert not whatsapp_service.verify_signature(SECRET, body, sign(body).removeprefix("sha256="))  # no prefix
    assert not whatsapp_service.verify_signature("", body, sign(body, ""))  # empty secret never verifies


def test_unsigned_or_badly_signed_posts_are_rejected_and_ignored(wa):
    payload = envelope(text_message("hi"))

    assert wa.post(payload, signature=False).status_code == 403
    assert wa.post(payload, signature="sha256=deadbeef").status_code == 403
    assert wa.post(payload, signature=sign(json.dumps(payload).encode(), "wrong-secret")).status_code == 403
    assert wa.outbox.sent == []


def test_tampered_body_fails_the_signature(wa):
    original = json.dumps(envelope(text_message("hi"))).encode()
    tampered = original.replace(b"hi", b"ho")
    assert wa.post(None, body=tampered, signature=sign(original)).status_code == 403


def test_posts_are_refused_when_no_app_secret_is_configured(wa):
    app.dependency_overrides[get_settings] = lambda: Settings(whatsapp_app_secret="", whatsapp_allow_unsigned=False)
    assert wa.post(envelope(text_message("hi")), signature=False).status_code == 403
    assert wa.outbox.sent == []


def test_unsigned_posts_are_allowed_only_with_the_explicit_local_testing_flag(wa):
    app.dependency_overrides[get_settings] = lambda: Settings(whatsapp_app_secret="", whatsapp_allow_unsigned=True)
    assert wa.post(envelope(text_message("hi")), signature=False).status_code == 200
    assert isinstance(wa.outbox.last, ButtonMessage)


def test_the_flag_never_weakens_verification_when_a_secret_is_set(wa):
    app.dependency_overrides[get_settings] = lambda: Settings(whatsapp_app_secret=SECRET, whatsapp_allow_unsigned=True)
    assert wa.post(envelope(text_message("hi")), signature=False).status_code == 403


# --- parsing ---------------------------------------------------------------------------


def parse(*messages: dict):
    payload = WAWebhookPayload.model_validate(envelope(*messages))
    return whatsapp_service.extract_inbound_messages(payload)


def test_text_messages_are_parsed_with_a_normalized_phone():
    (message,) = parse(text_message("  hi  ", message_id="wamid.X"))
    assert (message.kind, message.text, message.phone, message.message_id) == ("text", "  hi  ", PHONE_A, "wamid.X")


def test_button_and_list_replies_are_parsed_by_id_not_title():
    button, listing = parse(button_reply("menu:book"), list_reply("svc:2"))
    assert (button.kind, button.reply_id) == ("reply", "menu:book")
    assert (listing.kind, listing.reply_id) == ("reply", "svc:2")


def test_unsupported_message_types_are_flagged():
    image = {"from": WA_ID_A, "id": "wamid.I", "timestamp": "1", "type": "image", "image": {"id": "1"}}
    location = {"from": WA_ID_A, "id": "wamid.LOC", "timestamp": "1", "type": "location", "location": {}}
    assert [m.kind for m in parse(image, location)] == ["unsupported", "unsupported"]


def test_very_long_text_is_truncated():
    (message,) = parse(text_message("x" * 5000))
    assert len(message.text) == whatsapp_service.MAX_TEXT_LENGTH


def test_events_that_are_not_messages_yield_nothing():
    statuses = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": {"statuses": [{"id": "wamid.S", "status": "read"}]}}]}],
    }
    other_field = {"entry": [{"changes": [{"field": "account_update", "value": {"event": "X"}}]}]}
    for raw in (statuses, other_field, {}):
        assert whatsapp_service.extract_inbound_messages(WAWebhookPayload.model_validate(raw)) == []


# --- receiving through the webhook ----------------------------------------------------


def test_incoming_hi_gets_the_main_menu(wa):
    assert wa.post(envelope(text_message("hi"))).json() == {"status": "received"}

    to, message = wa.outbox.sent[-1]
    assert to == PHONE_A
    assert isinstance(message, ButtonMessage) and "Welcome to Astra Dental Clinic" in message.body


def test_interactive_replies_drive_the_state_machine(wa):
    wa.send(text_message("hi"))
    assert isinstance(wa.send(button_reply("menu:book")), ListMessage)
    assert [r.id for r in wa.send(list_reply("svc:1")).rows][-1] == "doc:any"


def test_status_only_webhooks_are_accepted_and_ignored(wa):
    statuses = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {"statuses": [{"id": "x", "status": "delivered"}]}}]}]}
    assert wa.post(statuses).status_code == 200
    assert wa.outbox.sent == []


def test_invalid_json_is_a_400_and_unexpected_shapes_are_ignored(wa):
    garbage = b"not json at all"
    assert wa.post(None, body=garbage, signature=sign(garbage)).status_code == 400

    odd = json.dumps({"entry": "not-a-list"}).encode()
    response = wa.post(None, body=odd, signature=sign(odd))
    assert response.status_code == 200 and response.json() == {"status": "ignored"}


def test_duplicate_deliveries_are_processed_once(wa):
    message = text_message("hi", message_id="wamid.SAME")

    wa.post(envelope(message))
    wa.post(envelope(message))  # Meta retrying the same webhook

    assert len(wa.outbox.sent) == 1
    with wa.session_factory() as db:
        assert db.query(ProcessedMessage).filter_by(id="wamid.SAME").count() == 1


def test_several_messages_in_one_webhook_are_handled_in_order(wa):
    wa.post(envelope(text_message("hi"), button_reply("menu:book")))

    kinds = [type(m).__name__ for _, m in wa.outbox.sent]
    assert kinds == ["ButtonMessage", "ListMessage"]


def test_a_failure_in_one_message_sends_a_fallback_and_does_not_break_the_next(wa, monkeypatch):
    calls = {"n": 0}
    original = whatsapp_service.ConversationService.handle

    def flaky(self, message):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(self, message)

    monkeypatch.setattr(whatsapp_service.ConversationService, "handle", flaky)

    wa.post(envelope(text_message("hi"), text_message("hi again")))

    assert isinstance(wa.outbox.sent[0][1], TextMessage) and "something went wrong" in wa.outbox.sent[0][1].body
    assert isinstance(wa.outbox.sent[1][1], ButtonMessage)


def test_full_booking_journey_over_signed_webhooks(wa):
    """The Definition of Done, minus Meta itself: signed webhooks in, real bookings out."""
    wa.send(text_message("Hi"))
    wa.send(button_reply("menu:book"))
    wa.send(list_reply("svc:1"))
    wa.send(list_reply("doc:1"))
    dates = wa.send(button_reply("date:other"))
    slots = wa.send(list_reply(dates.rows[0].id))  # the first date that really has availability
    slot_id = slots.rows[0].id  # ...and its first real slot (dates are relative to today)
    assert slot_id.startswith("slot:")
    wa.send(list_reply(slot_id))
    summary = wa.send(text_message("Rahul Sharma"))
    assert "Patient: Rahul Sharma" in summary.body

    confirmed = wa.send(button_reply("book:confirm"))
    assert "Appointment Confirmed" in confirmed.body and "Booking ID: AST-1001" in confirmed.body

    with wa.session_factory() as db:
        appointment = db.query(Appointment).one()
        assert appointment.patient.phone == PHONE_A  # taken from the webhook's `from`
        assert appointment.patient.name == "Rahul Sharma"
        assert appointment.start_datetime.isoformat(timespec="minutes") == slot_id.removeprefix("slot:")

    view = wa.send(button_reply("menu:view"))
    assert "AST-1001" in view.body

    wa.send(button_reply("manage:1"))
    wa.send(button_reply("cancel:1"))
    cancelled = wa.send(button_reply("cancel_confirm:1"))
    assert "Appointment Cancelled" in cancelled.body
    with wa.session_factory() as db:
        assert db.query(Appointment).one().status.value == "CANCELLED"
        assert db.query(ConversationSession).one().phone == PHONE_A


def test_a_different_sender_cannot_touch_the_appointment(wa):
    wa.send(text_message("Hi"))
    wa.send(button_reply("menu:book"))
    wa.send(list_reply("svc:1"))
    wa.send(list_reply("doc:1"))
    dates = wa.send(button_reply("date:other"))
    slots = wa.send(list_reply(dates.rows[0].id))
    wa.send(list_reply(slots.rows[0].id))
    wa.send(text_message("Rahul Sharma"))
    wa.send(button_reply("book:confirm"))

    other = "919123456780"
    wa.send(text_message("hi", sender=other))
    reply = wa.send(button_reply("cancel:1", sender=other))

    assert "Appointment not found" in reply.body
    with wa.session_factory() as db:
        assert db.query(Appointment).one().status.value == "CONFIRMED"
        assert {s.phone for s in db.query(ConversationSession)} == {PHONE_A, PHONE_B}


# --- outbound payloads (Cloud API "messages" request bodies) -------------------------------


def test_text_payload():
    assert whatsapp_service.build_payload("+919876543210", TextMessage("Hello")) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "919876543210",
        "type": "text",
        "text": {"preview_url": False, "body": "Hello"},
    }


def test_reply_button_payload():
    message = ButtonMessage(
        body="How can we help?",
        buttons=(Button("menu:book", "Book Appointment"), Button("menu:view", "View Appointment")),
        footer="Footer text",
    )

    assert whatsapp_service.build_payload("+919876543210", message) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "919876543210",
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "How can we help?"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "menu:book", "title": "Book Appointment"}},
                    {"type": "reply", "reply": {"id": "menu:view", "title": "View Appointment"}},
                ]
            },
            "footer": {"text": "Footer text"},
        },
    }


def test_list_payload():
    message = ListMessage(
        body="Select a service",
        button_label="Select service",
        section_title="Services",
        rows=(ListRow("svc:1", "General Consultation", "30 min"), ListRow("svc:2", "Teeth Cleaning")),
    )

    payload = whatsapp_service.build_payload("+919876543210", message)

    assert payload["type"] == "interactive"
    assert payload["interactive"] == {
        "type": "list",
        "body": {"text": "Select a service"},
        "action": {
            "button": "Select service",
            "sections": [
                {
                    "title": "Services",
                    "rows": [
                        {"id": "svc:1", "title": "General Consultation", "description": "30 min"},
                        {"id": "svc:2", "title": "Teeth Cleaning"},  # no empty description sent
                    ],
                }
            ],
        },
    }


def test_payloads_are_clipped_to_whatsapp_limits_as_a_safety_net():
    long_name = "X" * 100
    buttons = ButtonMessage(
        body="b" * 2000,
        buttons=tuple(Button(f"id{i}", long_name) for i in range(5)),
        footer="f" * 100,
    )
    payload = whatsapp_service.build_payload("+1", buttons)["interactive"]
    assert len(payload["body"]["text"]) == 1024
    assert len(payload["action"]["buttons"]) == 3
    assert all(len(b["reply"]["title"]) == 20 for b in payload["action"]["buttons"])
    assert len(payload["footer"]["text"]) == 60

    rows = tuple(ListRow(f"r{i}", long_name, long_name) for i in range(15))
    listing = ListMessage(body="x", button_label=long_name, rows=rows, section_title=long_name)
    payload = whatsapp_service.build_payload("+1", listing)["interactive"]
    sent_rows = payload["action"]["sections"][0]["rows"]
    assert len(sent_rows) == 10
    assert all(len(r["title"]) == 24 and len(r["description"]) == 72 for r in sent_rows)
    assert len(payload["action"]["button"]) == 20 and len(payload["action"]["sections"][0]["title"]) == 24


# --- the outbound client ---------------------------------------------------------------


def make_client(**overrides) -> whatsapp_service.WhatsAppClient:
    settings = Settings(whatsapp_access_token="TOKEN-123", whatsapp_phone_number_id="PNID", **overrides)
    return whatsapp_service.WhatsAppClient(settings)


def test_client_without_credentials_only_logs(caplog):
    client = whatsapp_service.WhatsAppClient(Settings(whatsapp_access_token="", whatsapp_phone_number_id=""))
    with caplog.at_level("INFO"):
        assert client.send(PHONE_A, TextMessage("hello")) is True
    assert "dry-run" in caplog.text
    assert "+91*******210" in caplog.text and PHONE_A not in caplog.text  # phone numbers are masked


def test_client_posts_to_the_graph_api_with_a_bearer_token():
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})

    client = make_client(whatsapp_api_version="v23.0")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))

    assert client.send(PHONE_A, TextMessage("hello")) is True
    assert seen["url"] == "https://graph.facebook.com/v23.0/PNID/messages"
    assert seen["auth"] == "Bearer TOKEN-123"
    assert seen["body"]["to"] == "919876543210" and seen["body"]["text"]["body"] == "hello"


def test_client_reports_api_errors_without_leaking_the_token(caplog):
    import httpx

    client = make_client()
    client._http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json={"error": {"message": "Bad recipient"}}))
    )

    with caplog.at_level("ERROR"):
        assert client.send(PHONE_A, TextMessage("hello")) is False
    assert "Bad recipient" in caplog.text
    assert "TOKEN-123" not in caplog.text


def test_client_survives_network_errors(caplog):
    import httpx

    def boom(request):
        raise httpx.ConnectError("no route to host")

    client = make_client()
    client._http = httpx.Client(transport=httpx.MockTransport(boom))

    with caplog.at_level("ERROR"):
        assert client.send(PHONE_A, TextMessage("hello")) is False
    assert "TOKEN-123" not in caplog.text
