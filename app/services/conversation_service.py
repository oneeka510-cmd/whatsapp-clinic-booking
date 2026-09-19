"""Deterministic WhatsApp conversation engine.

Turns one inbound patient message (typed text or a button/list tap) into outbound messages,
using a per-phone state machine persisted in `conversation_sessions`. There is no AI and no
booking logic in here: every decision about availability, booking, cancelling and
rescheduling is delegated to `availability_service` / `booking_service`.

Reply ids look like `action:argument` (`svc:3`, `slot:2026-09-21T17:30`, ...).

  * Some actions are *global* - valid in any state because they carry everything needed and are
    re-validated against the database: `menu:*`, `manage:<id>`, `reschedule:<id>`, `cancel:<id>`.
    (Old WhatsApp messages keep their buttons forever, so these must never misbehave.)
  * Everything else is *state-bound*: it is only honoured in the state that asked the question.
    Anything unexpected (typed text where a tap was expected, a tap from a stale message...)
    simply repeats the current question, freshly computed.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Appointment, AppointmentStatus, ConversationSession
from app.schemas.messages import MAX_LIST_ROWS, InboundMessage, OutboundMessage, TextMessage
from app.services import availability_service, booking_service, catalog_service, replies
from app.services.errors import BookingError, InvalidRequestError, NotFoundError, SlotUnavailableError
from app.utils.datetime_utils import format_date_full, format_time, now_local, parse_user_date

logger = logging.getLogger(__name__)

Flow = Literal["book", "reschedule"]


class State(str, Enum):
    MAIN_MENU = "MAIN_MENU"

    BOOK_SELECT_SERVICE = "BOOK_SELECT_SERVICE"
    BOOK_SELECT_DOCTOR = "BOOK_SELECT_DOCTOR"
    BOOK_SELECT_DATE = "BOOK_SELECT_DATE"
    BOOK_SELECT_SLOT = "BOOK_SELECT_SLOT"
    BOOK_ENTER_NAME = "BOOK_ENTER_NAME"
    BOOK_CONFIRM = "BOOK_CONFIRM"

    MANAGE_SELECT_APPOINTMENT = "MANAGE_SELECT_APPOINTMENT"
    MANAGE_SELECT_ACTION = "MANAGE_SELECT_ACTION"
    CANCEL_CONFIRM = "CANCEL_CONFIRM"

    RESCHEDULE_SELECT_DATE = "RESCHEDULE_SELECT_DATE"
    RESCHEDULE_SELECT_SLOT = "RESCHEDULE_SELECT_SLOT"
    RESCHEDULE_CONFIRM = "RESCHEDULE_CONFIRM"


_DATE_STATE: dict[Flow, State] = {"book": State.BOOK_SELECT_DATE, "reschedule": State.RESCHEDULE_SELECT_DATE}
_SLOT_STATE: dict[Flow, State] = {"book": State.BOOK_SELECT_SLOT, "reschedule": State.RESCHEDULE_SELECT_SLOT}

# Typing any of these at any point returns to the main menu.
RESET_WORDS = {"hi", "hello", "hey", "menu", "start", "restart", "home", "main menu"}

SLOT_TAKEN_TEXT = "Sorry, this appointment was just booked.\n\nPlease select another available time."
DEFAULT_HINT = "Sorry, I didn't understand that. Please choose one of the options below."

Handler = Callable[[ConversationSession, InboundMessage], list[OutboundMessage]]


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _split_reply(message: InboundMessage) -> tuple[str, str]:
    """`svc:3` -> ("svc", "3"); anything that is not a button/list tap -> ("", "")."""
    if message.kind != "reply":
        return "", ""
    action, _, argument = message.reply_id.partition(":")
    return action, argument


class ConversationService:
    def __init__(self, db: Session, now: datetime | None = None):
        self.db = db
        self.now = now or now_local()
        self.settings = get_settings()
        self._clinic_name_cache: str | None = None

        self._handlers: dict[State, Handler] = {
            State.MAIN_MENU: self._on_main_menu,
            State.BOOK_SELECT_SERVICE: self._on_select_service,
            State.BOOK_SELECT_DOCTOR: self._on_select_doctor,
            State.BOOK_SELECT_DATE: lambda s, m: self._on_date(s, m, "book"),
            State.BOOK_SELECT_SLOT: lambda s, m: self._on_slot(s, m, "book"),
            State.BOOK_ENTER_NAME: self._on_enter_name,
            State.BOOK_CONFIRM: self._on_book_confirm,
            State.MANAGE_SELECT_APPOINTMENT: self._on_select_appointment,
            State.MANAGE_SELECT_ACTION: lambda s, m: self._reprompt(s),
            State.CANCEL_CONFIRM: self._on_cancel_confirm,
            State.RESCHEDULE_SELECT_DATE: lambda s, m: self._on_date(s, m, "reschedule"),
            State.RESCHEDULE_SELECT_SLOT: lambda s, m: self._on_slot(s, m, "reschedule"),
            State.RESCHEDULE_CONFIRM: self._on_reschedule_confirm,
        }
        # How to re-ask the question of each state (used when the patient sends something unexpected).
        self._prompts: dict[State, Callable[[ConversationSession], list[OutboundMessage]]] = {
            State.MAIN_MENU: lambda s: [replies.main_menu(self._clinic_name())],
            State.BOOK_SELECT_SERVICE: self._prompt_services,
            State.BOOK_SELECT_DOCTOR: self._prompt_doctors,
            State.BOOK_SELECT_DATE: lambda s: self._ask_date(s, "book"),
            State.BOOK_SELECT_SLOT: lambda s: self._prompt_slots(s, "book"),
            State.BOOK_ENTER_NAME: lambda s: [replies.ask_name()],
            State.BOOK_CONFIRM: lambda s: [self._booking_summary(s)],
            State.MANAGE_SELECT_APPOINTMENT: lambda s: self._start_appointment_list(s, s.context.get("mode", "manage")),
            State.MANAGE_SELECT_ACTION: lambda s: self._open_action_menu(s, str(s.context["appointment_id"])),
            State.CANCEL_CONFIRM: lambda s: self._start_cancel(s, str(s.context["appointment_id"])),
            State.RESCHEDULE_SELECT_DATE: lambda s: self._ask_date(s, "reschedule"),
            State.RESCHEDULE_SELECT_SLOT: lambda s: self._prompt_slots(s, "reschedule"),
            State.RESCHEDULE_CONFIRM: lambda s: [self._reschedule_summary(s)],
        }

    # ------------------------------------------------------------------ entry point

    def handle(self, message: InboundMessage) -> list[OutboundMessage]:
        session = self._load_session(message.phone)
        try:
            replies_out = self._route(session, message)
        except BookingError as exc:
            logger.info("Conversation step failed with a business error: %s", exc)
            replies_out = self._main_menu(session, f"Sorry, we couldn't complete that. {exc}")
        except KeyError:
            logger.exception("Conversation context was incomplete; resetting the session")
            replies_out = self._main_menu(session)
        session.updated_at = self.now
        self.db.commit()
        return replies_out

    # ------------------------------------------------------------------ session helpers

    def _load_session(self, phone: str) -> ConversationSession:
        session = self.db.scalar(select(ConversationSession).where(ConversationSession.phone == phone))
        if session is None:
            session = ConversationSession(phone=phone, state=State.MAIN_MENU.value, updated_at=self.now)
            session.context = {}
            self.db.add(session)
        elif self.now - session.updated_at > timedelta(minutes=self.settings.session_timeout_minutes):
            session.state = State.MAIN_MENU.value
            session.context = {}
        return session

    @staticmethod
    def _goto(session: ConversationSession, state: State, context: dict | None = None) -> None:
        session.state = state.value
        if context is not None:
            session.context = context

    @staticmethod
    def _update_context(session: ConversationSession, **values) -> dict:
        context = session.context
        context.update(values)
        session.context = context
        return context

    def _clinic_name(self) -> str:
        if self._clinic_name_cache is None:
            clinic = catalog_service.get_clinic(self.db)
            self._clinic_name_cache = clinic.name if clinic else "our clinic"
        return self._clinic_name_cache

    def _load_active_appointment(self, session: ConversationSession, appointment_id: int) -> Appointment:
        """An upcoming, confirmed appointment that belongs to this phone number - or NotFoundError."""
        appointment = booking_service.get_appointment(self.db, appointment_id, phone=session.phone)
        if appointment.status != AppointmentStatus.CONFIRMED or appointment.start_datetime <= self.now:
            raise NotFoundError("That appointment is no longer active.")
        return appointment

    # ------------------------------------------------------------------ routing

    def _route(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        if message.kind == "text" and message.text.strip().lower() in RESET_WORDS:
            return self._main_menu(session)
        if message.kind == "unsupported":
            return self._reprompt(session, "Sorry, I can only read text messages and button taps.")

        action, argument = _split_reply(message)
        if action == "menu":
            return self._on_menu_action(session, argument)
        if action == "manage":
            return self._open_action_menu(session, argument)
        if action == "reschedule":
            return self._start_reschedule(session, argument)
        if action == "cancel":
            return self._start_cancel(session, argument)

        try:
            state = State(session.state)
        except ValueError:
            return self._main_menu(session)
        return self._handlers[state](session, message)

    def _reprompt(self, session: ConversationSession, hint: str = DEFAULT_HINT) -> list[OutboundMessage]:
        return [TextMessage(hint), *self._prompts[State(session.state)](session)]

    def _main_menu(self, session: ConversationSession, notice: str | None = None) -> list[OutboundMessage]:
        self._goto(session, State.MAIN_MENU, {})
        return [replies.main_menu(self._clinic_name(), notice)]

    def _on_menu_action(self, session: ConversationSession, argument: str) -> list[OutboundMessage]:
        if argument == "book":
            return self._start_booking(session)
        if argument in ("view", "manage"):
            return self._start_appointment_list(session, argument)
        return self._main_menu(session)

    def _on_main_menu(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        if message.kind == "reply":  # a button from an old message
            return self._main_menu(session, "Sorry, that option has expired.")
        return self._main_menu(session)

    # ------------------------------------------------------------------ booking: service, doctor

    def _start_booking(self, session: ConversationSession) -> list[OutboundMessage]:
        if not catalog_service.list_services(self.db):
            return self._main_menu(session, "Sorry, no services are available right now.")
        self._goto(session, State.BOOK_SELECT_SERVICE, {})
        return self._prompt_services(session)

    def _prompt_services(self, _session: ConversationSession) -> list[OutboundMessage]:
        return [replies.service_list(catalog_service.list_services(self.db)[:MAX_LIST_ROWS])]

    def _on_select_service(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        action, argument = _split_reply(message)
        service_id = _to_int(argument) if action == "svc" else None
        if service_id is None:
            return self._reprompt(session)
        try:
            service = catalog_service.get_active_service(self.db, service_id)
        except NotFoundError:
            return self._reprompt(session)

        if not catalog_service.list_doctors(self.db, service_id=service.id):
            return self._reprompt(session, "Sorry, no doctors currently offer that service. Please pick another.")
        self._goto(session, State.BOOK_SELECT_DOCTOR, {"service_id": service.id})
        return self._prompt_doctors(session)

    def _prompt_doctors(self, session: ConversationSession) -> list[OutboundMessage]:
        doctors = catalog_service.list_doctors(self.db, service_id=session.context["service_id"])
        offer_any = len(doctors) > 1
        shown = doctors[: MAX_LIST_ROWS - 1] if offer_any else doctors[:MAX_LIST_ROWS]
        return [replies.doctor_list(shown, allow_any=offer_any)]

    def _on_select_doctor(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        action, argument = _split_reply(message)
        if action != "doc":
            return self._reprompt(session)

        doctors = catalog_service.list_doctors(self.db, service_id=session.context["service_id"])
        if argument == "any" and len(doctors) > 1:
            doctor_id = None  # decided per slot, from whoever is free
        else:
            doctor_id = _to_int(argument)
            if doctor_id not in {doctor.id for doctor in doctors}:
                return self._reprompt(session)

        self._update_context(session, doctor_id=doctor_id)
        return self._ask_date(session, "book")

    # ------------------------------------------------------------------ shared: date and slot steps

    def _flow_params(self, session: ConversationSession, flow: Flow) -> tuple[int, int | None, int | None]:
        """(service_id, doctor_id or None for any doctor, appointment id to ignore) for a flow."""
        context = session.context
        if flow == "reschedule":
            appointment = self._load_active_appointment(session, context["appointment_id"])
            return appointment.service_id, appointment.doctor_id, appointment.id
        return context["service_id"], context.get("doctor_id"), None

    def _slot_map(self, session: ConversationSession, day: date, flow: Flow) -> dict[datetime, list[int]]:
        service_id, doctor_id, exclude_id = self._flow_params(session, flow)
        return availability_service.get_slot_doctors(
            self.db,
            service_id=service_id,
            day=day,
            doctor_id=doctor_id,
            now=self.now,
            exclude_appointment_id=exclude_id,
        )

    def _ask_date(self, session: ConversationSession, flow: Flow) -> list[OutboundMessage]:
        session.state = _DATE_STATE[flow].value
        if flow == "reschedule":
            appointment = self._load_active_appointment(session, session.context["appointment_id"])
            body = (
                f"*Reschedule {appointment.booking_reference}*\n"
                f"Currently: {format_date_full(appointment.start_datetime.date())}, "
                f"{format_time(appointment.start_datetime)}\n\n"
                "Which day would you like instead?"
            )
        else:
            body = f"📅 When would you like to come in?\n\nToday is {format_date_full(self.now.date())}."
        return [replies.date_choice(body)]

    def _on_date(self, session: ConversationSession, message: InboundMessage, flow: Flow) -> list[OutboundMessage]:
        today = self.now.date()
        day: date | None = None

        if message.kind == "reply":
            action, argument = _split_reply(message)
            if action != "date":
                return self._reprompt(session)
            if argument == "today":
                day = today
            elif argument == "tomorrow":
                day = today + timedelta(days=1)
            elif argument == "other":
                return self._show_date_list(session, flow)
            else:
                day = _parse_iso_date(argument)
        else:
            day = parse_user_date(message.text)
            if day is None:
                return self._reprompt(
                    session, "Sorry, I couldn't read that date. Tap an option, or type a date like 25/09/2026."
                )

        if day is None:
            return self._reprompt(session)
        if day < today:
            return self._reprompt(session, "That date has already passed. Please choose another date.")
        window = self.settings.booking_window_days
        if day > today + timedelta(days=window):
            return self._reprompt(session, f"We can only book up to {window} days ahead. Please choose an earlier date.")
        return self._show_slots(session, day, flow, page=0)

    def _show_date_list(self, session: ConversationSession, flow: Flow) -> list[OutboundMessage]:
        service_id, doctor_id, exclude_id = self._flow_params(session, flow)
        dates = availability_service.get_bookable_dates(
            self.db,
            service_id=service_id,
            doctor_id=doctor_id,
            from_day=self.now.date() + timedelta(days=2),  # Today and Tomorrow are already buttons
            limit=MAX_LIST_ROWS,
            now=self.now,
            exclude_appointment_id=exclude_id,
        )
        if not dates:
            notice = TextMessage("Sorry, there are no other dates with availability right now.")
            return [notice, *self._ask_date(session, flow)]
        return [replies.date_list(dates)]

    def _show_slots(self, session: ConversationSession, day: date, flow: Flow, page: int) -> list[OutboundMessage]:
        slot_map = self._slot_map(session, day, flow)
        if not slot_map:
            notice = TextMessage(f"Sorry, there are no available appointments on {format_date_full(day)}.")
            return [notice, *self._ask_date(session, flow)]

        page_slots, page, pages = replies.paginate_slots(list(slot_map), page)
        self._update_context(session, date=day.isoformat(), slot_page=page)
        session.state = _SLOT_STATE[flow].value
        return [replies.slot_list(day, page_slots, page, pages)]

    def _prompt_slots(self, session: ConversationSession, flow: Flow) -> list[OutboundMessage]:
        context = session.context
        return self._show_slots(session, date.fromisoformat(context["date"]), flow, context.get("slot_page", 0))

    def _on_slot(self, session: ConversationSession, message: InboundMessage, flow: Flow) -> list[OutboundMessage]:
        action, argument = _split_reply(message)
        context = session.context
        day = date.fromisoformat(context["date"])

        if action == "slots":
            page = _to_int(argument)
            return self._reprompt(session) if page is None else self._show_slots(session, day, flow, page)
        if action != "slot":
            return self._reprompt(session)

        start = _parse_iso_datetime(argument)
        if start is None or start.date() != day:  # stale list from another date
            return self._reprompt(session)

        # Availability may have changed since the list was sent - check again.
        doctor_ids = self._slot_map(session, day, flow).get(start)
        if not doctor_ids:
            return [TextMessage(SLOT_TAKEN_TEXT), *self._show_slots(session, day, flow, page=0)]

        self._update_context(session, slot=start.strftime("%H:%M"), assigned_doctor_id=doctor_ids[0])
        if flow == "book":
            session.state = State.BOOK_ENTER_NAME.value
            return [replies.ask_name()]
        session.state = State.RESCHEDULE_CONFIRM.value
        return [self._reschedule_summary(session)]

    @staticmethod
    def _chosen_start(context: dict) -> datetime:
        return datetime.combine(date.fromisoformat(context["date"]), datetime.strptime(context["slot"], "%H:%M").time())

    # ------------------------------------------------------------------ booking: name, confirm

    def _on_enter_name(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        if message.kind != "text":
            return self._reprompt(session, "Please type the patient's full name.")
        try:
            name = booking_service.clean_patient_name(message.text)
        except InvalidRequestError as exc:
            return self._reprompt(session, f"{exc} Please try again.")

        self._update_context(session, patient_name=name)
        session.state = State.BOOK_CONFIRM.value
        return [self._booking_summary(session)]

    def _booking_summary(self, session: ConversationSession) -> OutboundMessage:
        context = session.context
        service = catalog_service.get_active_service(self.db, context["service_id"])
        doctor = catalog_service.get_active_doctor(self.db, context["assigned_doctor_id"])
        return replies.booking_summary(context["patient_name"], service, doctor, self._chosen_start(context))

    def _on_book_confirm(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        action, argument = _split_reply(message)
        if action != "book" or argument not in ("confirm", "cancel"):
            return self._reprompt(session)
        if argument == "cancel":
            return self._main_menu(session, "No problem - nothing was booked.")

        context = session.context
        try:
            appointment = booking_service.create_appointment(
                self.db,
                patient_name=context["patient_name"],
                phone=session.phone,
                doctor_id=context["assigned_doctor_id"],
                service_id=context["service_id"],
                start=self._chosen_start(context),
                now=self.now,
            )
        except SlotUnavailableError:
            # Someone else took it between the slot list and this Confirm tap.
            day = date.fromisoformat(context["date"])
            return [TextMessage(SLOT_TAKEN_TEXT), *self._show_slots(session, day, "book", page=0)]

        self._goto(session, State.MAIN_MENU, {})
        return [replies.booking_confirmed(appointment, self._clinic_name())]

    # ------------------------------------------------------------------ view / manage

    def _start_appointment_list(self, session: ConversationSession, mode: str) -> list[OutboundMessage]:
        appointments = booking_service.get_patient_appointments(
            self.db, phone=session.phone, upcoming_only=True, now=self.now
        )[:MAX_LIST_ROWS]
        if not appointments:
            return self._main_menu(session, "You don't have any upcoming appointments.")

        if len(appointments) == 1:
            if mode == "view":
                return self._show_appointment_details(session, appointments[0])
            return self._open_action_menu(session, str(appointments[0].id))

        self._goto(session, State.MANAGE_SELECT_APPOINTMENT, {"mode": mode})
        return [replies.appointment_list(appointments, mode)]

    def _on_select_appointment(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        action, argument = _split_reply(message)
        appointment_id = _to_int(argument) if action == "appt" else None
        if appointment_id is None:
            return self._reprompt(session)

        appointment = self._load_active_appointment(session, appointment_id)
        if session.context.get("mode") == "view":
            return self._show_appointment_details(session, appointment)
        return self._open_action_menu(session, str(appointment.id))

    def _show_appointment_details(self, session: ConversationSession, appointment: Appointment) -> list[OutboundMessage]:
        self._goto(session, State.MAIN_MENU, {})
        return [replies.appointment_details(appointment)]

    def _open_action_menu(self, session: ConversationSession, argument: str) -> list[OutboundMessage]:
        appointment_id = _to_int(argument)
        if appointment_id is None:
            return self._main_menu(session)
        appointment = self._load_active_appointment(session, appointment_id)
        self._goto(session, State.MANAGE_SELECT_ACTION, {"appointment_id": appointment.id})
        return [replies.action_menu(appointment)]

    # ------------------------------------------------------------------ cancel

    def _start_cancel(self, session: ConversationSession, argument: str) -> list[OutboundMessage]:
        appointment_id = _to_int(argument)
        if appointment_id is None:
            return self._main_menu(session)
        appointment = self._load_active_appointment(session, appointment_id)
        self._goto(session, State.CANCEL_CONFIRM, {"appointment_id": appointment.id})
        return [replies.cancel_confirm(appointment)]

    def _on_cancel_confirm(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        action, argument = _split_reply(message)
        appointment_id = session.context["appointment_id"]
        if action not in ("cancel_confirm", "cancel_keep") or _to_int(argument) != appointment_id:
            return self._reprompt(session)

        if action == "cancel_keep":
            return self._main_menu(session, "👍 Your appointment has been kept.")

        appointment = booking_service.cancel_appointment(
            self.db, appointment_id, phone=session.phone, now=self.now
        )
        self._goto(session, State.MAIN_MENU, {})
        return [replies.cancelled(appointment)]

    # ------------------------------------------------------------------ reschedule

    def _start_reschedule(self, session: ConversationSession, argument: str) -> list[OutboundMessage]:
        appointment_id = _to_int(argument)
        if appointment_id is None:
            return self._main_menu(session)
        appointment = self._load_active_appointment(session, appointment_id)
        self._goto(session, State.RESCHEDULE_SELECT_DATE, {"appointment_id": appointment.id})
        return self._ask_date(session, "reschedule")

    def _reschedule_summary(self, session: ConversationSession) -> OutboundMessage:
        context = session.context
        appointment = self._load_active_appointment(session, context["appointment_id"])
        return replies.reschedule_summary(appointment, self._chosen_start(context))

    def _on_reschedule_confirm(self, session: ConversationSession, message: InboundMessage) -> list[OutboundMessage]:
        action, _ = _split_reply(message)
        if action not in ("reschedule_confirm", "reschedule_abort"):
            return self._reprompt(session)
        if action == "reschedule_abort":
            return self._main_menu(session, "No changes were made to your appointment.")

        context = session.context
        try:
            appointment = booking_service.reschedule_appointment(
                self.db,
                context["appointment_id"],
                phone=session.phone,
                new_start=self._chosen_start(context),
                now=self.now,
            )
        except SlotUnavailableError:
            day = date.fromisoformat(context["date"])
            return [TextMessage(SLOT_TAKEN_TEXT), *self._show_slots(session, day, "reschedule", page=0)]

        self._goto(session, State.MAIN_MENU, {})
        return [replies.rescheduled(appointment, self._clinic_name())]
