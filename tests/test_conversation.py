"""The WhatsApp conversation flow, driven message-by-message without any WhatsApp involved."""

from datetime import datetime, timedelta
from itertools import count

import pytest

from app.models import Appointment, AppointmentStatus, ConversationSession
from app.schemas.messages import (
    MAX_BUTTONS,
    MAX_LIST_ROWS,
    ButtonMessage,
    InboundMessage,
    ListMessage,
    TextMessage,
)
from app.services import availability_service as availability
from app.services import booking_service as booking
from app.services import replies
from app.services.conversation_service import SLOT_TAKEN_TEXT, ConversationService
from tests.conftest import (
    DR_MEHTA,
    DR_SHARMA,
    GENERAL_CONSULTATION,
    MONDAY,
    NOW,
    PHONE_A,
    PHONE_B,
    at,
)

TUESDAY = MONDAY + timedelta(days=1)
_message_ids = count(1)


def check_whatsapp_limits(message) -> None:
    """Every message we send must be accepted by the Cloud API (limits from Meta's docs)."""
    if isinstance(message, TextMessage):
        assert 0 < len(message.body) <= 4096
        return
    assert 0 < len(message.body) <= 1024
    assert len(message.footer) <= 60
    if isinstance(message, ButtonMessage):
        assert 1 <= len(message.buttons) <= MAX_BUTTONS
        for button in message.buttons:
            assert 0 < len(button.title) <= 20, button.title
            assert len(button.id) <= 256
    else:
        assert isinstance(message, ListMessage)
        assert 1 <= len(message.rows) <= MAX_LIST_ROWS
        assert 0 < len(message.button_label) <= 20
        assert len(message.section_title) <= 24
        for row in message.rows:
            assert 0 < len(row.title) <= 24, row.title
            assert len(row.description) <= 72, row.description
            assert len(row.id) <= 200


class Chat:
    """One patient's WhatsApp chat with the bot."""

    def __init__(self, db, phone=PHONE_A, now=NOW):
        self.db, self.phone, self.now = db, phone, now
        self.last: list = []

    def _send(self, **fields) -> list:
        message = InboundMessage(phone=self.phone, message_id=f"wamid.{next(_message_ids)}", **fields)
        self.last = ConversationService(self.db, now=self.now).handle(message)
        for outbound in self.last:
            check_whatsapp_limits(outbound)
        return self.last

    def say(self, text: str) -> list:
        return self._send(kind="text", text=text)

    def tap(self, reply_id: str) -> list:
        return self._send(kind="reply", reply_id=reply_id)

    @property
    def state(self) -> str:
        self.db.expire_all()
        return self.db.query(ConversationSession).filter_by(phone=self.phone).one().state

    @property
    def main(self):
        """The interactive message at the end of the last reply (the actual question)."""
        return self.last[-1]

    @property
    def text(self) -> str:
        """All text of the last reply, for simple 'contains' assertions."""
        return "\n".join(m.body for m in self.last)

    @property
    def ids(self) -> list[str]:
        return option_ids(self.main)

    def all_offered_slots(self) -> list[str]:
        """Walk every page of the current slot list and collect the slot ids offered."""
        offered: list[str] = []
        page = 0
        while True:
            self.tap(f"slots:{page}")
            offered += [i for i in self.ids if i.startswith("slot:")]
            if f"slots:{page + 1}" not in self.ids:
                return offered
            page += 1

    def go_to_slots(self, service="svc:1", doctor="doc:1", date="date:2026-09-21"):
        self.say("hi")
        self.tap("menu:book")
        self.tap(service)
        self.tap(doctor)
        self.tap(date)
        return self


def option_ids(message) -> list[str]:
    if isinstance(message, ButtonMessage):
        return [b.id for b in message.buttons]
    if isinstance(message, ListMessage):
        return [r.id for r in message.rows]
    return []


def book_via_chat(chat: Chat, slot="slot:2026-09-21T17:30", name="Rahul Sharma", **kwargs) -> Appointment:
    chat.go_to_slots(**kwargs)
    if slot not in chat.ids:
        chat.tap("slots:1")
    chat.tap(slot)
    chat.say(name)
    chat.tap("book:confirm")
    return chat.db.query(Appointment).order_by(Appointment.id.desc()).first()


@pytest.fixture
def chat(db):
    return Chat(db)


# --- the Definition-of-Done journey ---------------------------------------------------


def test_greeting_shows_the_welcome_menu(chat):
    chat.say("hello there")

    assert isinstance(chat.main, ButtonMessage)
    assert "Welcome to Astra Dental Clinic" in chat.main.body
    assert chat.ids == ["menu:book", "menu:view", "menu:manage"]
    assert [b.title for b in chat.main.buttons] == ["Book Appointment", "View Appointment", "Manage Appointment"]


def test_complete_booking_journey(chat, db):
    chat.say("hi")

    chat.tap("menu:book")
    assert chat.state == "BOOK_SELECT_SERVICE"
    assert chat.ids == ["svc:1", "svc:2", "svc:3", "svc:4"]
    assert [r.title for r in chat.main.rows] == [
        "General Consultation", "Teeth Cleaning", "Root Canal", "Orthodontic Consultation",
    ]  # fmt: skip

    chat.tap("svc:1")
    assert chat.state == "BOOK_SELECT_DOCTOR"
    assert chat.ids == ["doc:1", "doc:2", "doc:3", "doc:any"]
    assert [r.title for r in chat.main.rows][-1] == "Any Available Doctor"

    chat.tap("doc:1")
    assert chat.state == "BOOK_SELECT_DATE"
    assert chat.ids == ["date:today", "date:tomorrow", "date:other"]
    assert [b.title for b in chat.main.buttons] == ["Today", "Tomorrow", "Choose another date"]

    chat.tap("date:2026-09-21")
    assert chat.state == "BOOK_SELECT_SLOT"
    assert "Monday, 21 September 2026" in chat.main.body
    assert chat.ids[0] == "slot:2026-09-21T09:00"

    chat.tap("slots:1")  # 16 slots don't fit one WhatsApp list, so the rest are on page 2
    assert "slot:2026-09-21T17:30" in chat.ids
    chat.tap("slot:2026-09-21T17:30")
    assert chat.state == "BOOK_ENTER_NAME"

    chat.say("Rahul Sharma")
    assert chat.state == "BOOK_CONFIRM"
    assert chat.main.body == (
        "*Confirm Appointment*\n\n"
        "Patient: Rahul Sharma\n"
        "Service: General Consultation\n"
        "Doctor: Dr. Ananya Sharma\n"
        "Date: 21 September 2026\n"
        "Time: 5:30 PM"
    )
    assert [b.title for b in chat.main.buttons] == ["Confirm", "Cancel"]
    assert db.query(Appointment).count() == 0  # nothing is stored until Confirm

    chat.tap("book:confirm")
    assert chat.main.body == (
        "✅ *Appointment Confirmed*\n\n"
        "Booking ID: AST-1001\n\n"
        "Dr. Ananya Sharma\n"
        "General Consultation\n\n"
        "21 September 2026\n"
        "5:30 PM\n\n"
        "Astra Dental Clinic\n\n"
        "Thank you!"
    )
    assert chat.ids == ["manage:1", "menu:main"]
    assert chat.state == "MAIN_MENU"

    stored = db.query(Appointment).one()
    assert (stored.patient.name, stored.patient.phone) == ("Rahul Sharma", PHONE_A)  # phone came from the webhook
    assert stored.start_datetime == at(MONDAY, "17:30") and stored.status == AppointmentStatus.CONFIRMED


def test_view_appointment_shows_the_new_booking(chat):
    book_via_chat(chat)

    chat.tap("menu:view")

    assert chat.main.body == (
        "*Your Upcoming Appointment*\n\n"
        "Booking ID: AST-1001\n\n"
        "Dr. Ananya Sharma\n"
        "General Consultation\n\n"
        "21 September 2026\n"
        "5:30 PM"
    )
    assert chat.ids == ["manage:1", "menu:main"]


def test_cancel_appointment_journey(chat, db):
    book_via_chat(chat)
    assert "17:30" not in slot_times(db)

    chat.tap("menu:manage")  # one appointment -> straight to its actions
    assert chat.state == "MANAGE_SELECT_ACTION"
    assert chat.ids == ["reschedule:1", "cancel:1", "menu:main"]

    chat.tap("cancel:1")
    assert chat.state == "CANCEL_CONFIRM"
    assert "Are you sure you want to cancel?" in chat.main.body
    assert [b.title for b in chat.main.buttons] == ["Yes, Cancel", "Keep Appointment"]
    assert db.get(Appointment, 1).status == AppointmentStatus.CONFIRMED  # not yet

    chat.tap("cancel_confirm:1")
    assert chat.main.body == "✅ *Appointment Cancelled*\n\nBooking ID: AST-1001"
    assert db.get(Appointment, 1).status == AppointmentStatus.CANCELLED
    assert "17:30" in slot_times(db)  # slot is immediately available again

    chat.tap("menu:view")
    assert "You don't have any upcoming appointments." in chat.main.body


def test_keep_appointment_leaves_it_untouched(chat, db):
    book_via_chat(chat)
    chat.tap("cancel:1")

    chat.tap("cancel_keep:1")

    assert db.get(Appointment, 1).status == AppointmentStatus.CONFIRMED
    assert "has been kept" in chat.main.body


def test_reschedule_journey(chat, db):
    book_via_chat(chat)

    chat.tap("menu:manage")
    chat.tap("reschedule:1")
    assert chat.state == "RESCHEDULE_SELECT_DATE"
    assert "AST-1001" in chat.main.body and "5:30 PM" in chat.main.body

    chat.tap("date:2026-09-22")
    assert chat.state == "RESCHEDULE_SELECT_SLOT"
    chat.tap("slot:2026-09-22T10:00")
    assert chat.state == "RESCHEDULE_CONFIRM"
    assert chat.main.body == (
        "*Confirm Reschedule*\n\n"
        "Booking ID: AST-1001\n"
        "Dr. Ananya Sharma\n"
        "General Consultation\n\n"
        "Current: 21 September 2026, 5:30 PM\n"
        "New: 22 September 2026, 10:00 AM"
    )
    assert db.get(Appointment, 1).start_datetime == at(MONDAY, "17:30")  # unchanged until Confirm

    chat.tap("reschedule_confirm")
    assert "Appointment Rescheduled" in chat.main.body and "22 September 2026\n10:00 AM" in chat.main.body

    db.expire_all()
    assert db.query(Appointment).count() == 1
    assert db.get(Appointment, 1).start_datetime == at(TUESDAY, "10:00")
    assert "17:30" in slot_times(db)  # old slot freed
    assert "10:00" not in slot_times(db, TUESDAY)  # new slot taken


def test_abandoning_a_reschedule_changes_nothing(chat, db):
    book_via_chat(chat)
    chat.tap("reschedule:1")
    chat.tap("date:2026-09-22")
    chat.tap("slot:2026-09-22T10:00")

    chat.tap("reschedule_abort")

    assert db.get(Appointment, 1).start_datetime == at(MONDAY, "17:30")
    assert "No changes were made" in chat.main.body


# --- double booking -------------------------------------------------------------------


def test_slot_taken_before_confirm_is_reported_and_availability_refreshed(chat, db):
    chat.go_to_slots()
    chat.tap("slots:1")
    chat.tap("slot:2026-09-21T17:30")
    chat.say("Rahul Sharma")
    assert chat.state == "BOOK_CONFIRM"

    # Meanwhile another patient books 17:30 with the same doctor.
    booking.create_appointment(
        db, patient_name="Fast Patient", phone=PHONE_B, doctor_id=DR_SHARMA,
        service_id=GENERAL_CONSULTATION, start=at(MONDAY, "17:30"), now=NOW,
    )  # fmt: skip

    chat.tap("book:confirm")

    assert chat.last[0].body == SLOT_TAKEN_TEXT
    assert chat.state == "BOOK_SELECT_SLOT"

    offered = chat.all_offered_slots()  # the refreshed list, all pages
    assert "slot:2026-09-21T17:30" not in offered
    assert len(offered) == 15 and "slot:2026-09-21T17:00" in offered

    # Only the fast patient's booking exists; our patient got nothing.
    (only,) = db.query(Appointment).all()
    assert only.patient.phone == PHONE_B


def test_slot_taken_between_list_and_tap_is_caught(chat, db):
    chat.go_to_slots()
    booking.create_appointment(
        db, patient_name="Fast Patient", phone=PHONE_B, doctor_id=DR_SHARMA,
        service_id=GENERAL_CONSULTATION, start=at(MONDAY, "09:00"), now=NOW,
    )  # fmt: skip

    chat.tap("slot:2026-09-21T09:00")

    assert chat.last[0].body == SLOT_TAKEN_TEXT
    assert chat.state == "BOOK_SELECT_SLOT"
    assert "slot:2026-09-21T09:00" not in chat.ids


def test_slot_taken_before_reschedule_confirm(chat, db):
    book_via_chat(chat)
    chat.tap("reschedule:1")
    chat.tap("date:2026-09-22")
    chat.tap("slot:2026-09-22T10:00")
    booking.create_appointment(
        db, patient_name="Fast Patient", phone=PHONE_B, doctor_id=DR_SHARMA,
        service_id=GENERAL_CONSULTATION, start=at(TUESDAY, "10:00"), now=NOW,
    )  # fmt: skip

    chat.tap("reschedule_confirm")

    assert chat.last[0].body == SLOT_TAKEN_TEXT
    assert chat.state == "RESCHEDULE_SELECT_SLOT"
    assert db.get(Appointment, 1).start_datetime == at(MONDAY, "17:30")  # the original booking is intact
    assert db.get(Appointment, 1).status == AppointmentStatus.CONFIRMED


def test_cancelling_at_the_confirmation_screen_books_nothing(chat, db):
    chat.go_to_slots()
    chat.tap("slot:2026-09-21T09:00")
    chat.say("Rahul Sharma")

    chat.tap("book:cancel")

    assert db.query(Appointment).count() == 0
    assert chat.state == "MAIN_MENU"
    assert chat.ids == ["menu:book", "menu:view", "menu:manage"]


# --- doctors, dates, pagination -------------------------------------------------------


def test_service_only_lists_compatible_doctors(chat):
    chat.say("hi")
    chat.tap("menu:book")

    chat.tap("svc:3")  # Root Canal: Sharma and Kapoor
    assert chat.ids == ["doc:1", "doc:3", "doc:any"]

    chat.say("menu")
    chat.tap("menu:book")
    chat.tap("svc:4")  # Orthodontic Consultation: only Mehta -> no "any" row
    assert chat.ids == ["doc:2"]


def test_any_available_doctor_picks_whoever_is_free(chat, db):
    booking.create_appointment(
        db, patient_name="Busy Patient", phone=PHONE_B, doctor_id=DR_SHARMA,
        service_id=GENERAL_CONSULTATION, start=at(MONDAY, "09:00"), now=NOW,
    )  # fmt: skip

    chat.go_to_slots(doctor="doc:any")
    chat.tap("slot:2026-09-21T09:00")  # Sharma is busy at 09:00, so this must go to Mehta
    chat.say("Rahul Sharma")
    assert "Doctor: Dr. Rohan Mehta" in chat.main.body
    chat.tap("book:confirm")

    assert db.query(Appointment).filter_by(patient_id=2).one().doctor_id == DR_MEHTA


def test_closed_day_offers_the_date_choice_again(chat):
    chat.say("hi")
    chat.tap("menu:book")
    chat.tap("svc:1")
    chat.tap("doc:1")

    chat.tap("date:tomorrow")  # Sunday 20 September

    assert "no available appointments on Sunday, 20 September 2026" in chat.text
    assert chat.state == "BOOK_SELECT_DATE"
    assert chat.ids == ["date:today", "date:tomorrow", "date:other"]


def test_today_only_offers_slots_that_have_not_started(chat):
    chat.say("hi")
    chat.tap("menu:book")
    chat.tap("svc:1")
    chat.tap("doc:1")

    chat.tap("date:today")  # Saturday, and it is 10:00

    assert chat.ids[0] == "slot:2026-09-19T10:30"


def test_choose_another_date_lists_only_dates_with_availability(chat):
    chat.say("hi")
    chat.tap("menu:book")
    chat.tap("svc:1")
    chat.tap("doc:1")

    chat.tap("date:other")

    assert isinstance(chat.main, ListMessage)
    assert chat.ids[0] == "date:2026-09-21"  # starts after tomorrow (Sunday is skipped anyway)
    assert "date:2026-09-27" not in chat.ids  # a Sunday: closed
    assert len(chat.ids) == MAX_LIST_ROWS
    assert chat.main.rows[0].title == "Mon, 21 Sep"


def test_a_date_can_be_typed(chat):
    chat.say("hi")
    chat.tap("menu:book")
    chat.tap("svc:1")
    chat.tap("doc:1")

    chat.say("21/09/2026")

    assert chat.state == "BOOK_SELECT_SLOT"
    assert "Monday, 21 September 2026" in chat.main.body


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("next tuesday", "couldn't read that date"),
        ("01/01/2020", "already passed"),
        ("01/01/2030", "days ahead"),
    ],
)
def test_bad_typed_dates_are_explained(chat, typed, expected):
    chat.say("hi")
    chat.tap("menu:book")
    chat.tap("svc:1")
    chat.tap("doc:1")

    chat.say(typed)

    assert expected in chat.last[0].body
    assert chat.state == "BOOK_SELECT_DATE"


def test_long_slot_lists_are_paginated_within_whatsapp_limits(chat):
    chat.go_to_slots()

    assert len(chat.ids) == 9 and chat.ids[-1] == "slots:1"  # 8 slots + "More times"
    assert "Page 1 of 2" in chat.main.body
    chat.tap("slots:1")
    assert len(chat.ids) == 9 and chat.ids[0] == "slots:0"  # "Earlier times" + 8 slots
    assert "Page 2 of 2" in chat.main.body

    listed = chat.all_offered_slots()
    assert len(listed) == 16 and len(set(listed)) == 16


def test_paginate_slots_boundaries():
    slots = [datetime(2026, 9, 21, 9, 0) + timedelta(minutes=30 * i) for i in range(30)]

    assert replies.paginate_slots(slots[:10], 0) == (slots[:10], 0, 1)  # fits one list exactly
    page, index, pages = replies.paginate_slots(slots[:11], 0)
    assert (len(page), index, pages) == (8, 0, 2)
    page, index, pages = replies.paginate_slots(slots[:11], 1)
    assert (len(page), index, pages) == (3, 1, 2)
    assert replies.paginate_slots(slots[:11], 99)[1] == 1  # clamped
    assert replies.paginate_slots(slots[:11], -5)[1] == 0


# --- view / manage with several appointments ------------------------------------------


def test_view_with_no_appointments(chat):
    chat.tap("menu:view")
    assert "You don't have any upcoming appointments." in chat.main.body
    assert chat.ids == ["menu:book", "menu:view", "menu:manage"]


def test_multiple_appointments_can_be_chosen_from_a_list(chat, db):
    first = book_via_chat(chat, slot="slot:2026-09-21T09:00")
    second = book_via_chat(chat, slot="slot:2026-09-21T14:00")

    chat.tap("menu:view")
    assert chat.state == "MANAGE_SELECT_APPOINTMENT"
    assert chat.ids == [f"appt:{first.id}", f"appt:{second.id}"]
    assert chat.main.rows[0].title == "Mon, 21 Sep · 9:00 AM"
    assert chat.main.rows[0].description == "AST-1001 · General Consultation · Dr. Ananya Sharma"

    chat.tap(f"appt:{second.id}")
    assert "AST-1002" in chat.main.body and "2:00 PM" in chat.main.body

    chat.tap("menu:manage")
    chat.tap(f"appt:{first.id}")
    assert chat.state == "MANAGE_SELECT_ACTION"
    assert "AST-1001" in chat.main.body


# --- security -------------------------------------------------------------------------


@pytest.mark.parametrize("forged", ["manage:1", "cancel:1", "reschedule:1"])
def test_a_patient_cannot_reach_another_patients_appointment(db, forged):
    owner = Chat(db, phone=PHONE_A)
    book_via_chat(owner)
    attacker = Chat(db, phone=PHONE_B)
    attacker.say("hi")

    attacker.tap(forged)  # a hand-crafted button id for someone else's appointment

    assert "Appointment not found" in attacker.text
    assert attacker.state == "MAIN_MENU"
    assert db.get(Appointment, 1).status == AppointmentStatus.CONFIRMED


def test_forged_cancel_confirmation_is_ignored_without_the_matching_state(db):
    owner = Chat(db, phone=PHONE_A)
    book_via_chat(owner)
    attacker = Chat(db, phone=PHONE_B)
    attacker.say("hi")

    attacker.tap("cancel_confirm:1")

    assert db.get(Appointment, 1).status == AppointmentStatus.CONFIRMED


def test_cancel_confirm_must_match_the_appointment_being_confirmed(chat, db):
    first = book_via_chat(chat, slot="slot:2026-09-21T09:00")
    book_via_chat(chat, slot="slot:2026-09-21T14:00")
    chat.tap(f"cancel:{first.id}")

    chat.tap("cancel_confirm:2")  # a different booking than the one being confirmed

    assert db.get(Appointment, 1).status == AppointmentStatus.CONFIRMED
    assert db.get(Appointment, 2).status == AppointmentStatus.CONFIRMED
    assert chat.state == "CANCEL_CONFIRM"


def test_sessions_are_isolated_per_phone(db):
    a, b = Chat(db, phone=PHONE_A), Chat(db, phone=PHONE_B)
    a.say("hi")
    a.tap("menu:book")
    b.say("hi")

    assert (a.state, b.state) == ("BOOK_SELECT_SERVICE", "MAIN_MENU")


# --- robustness -----------------------------------------------------------------------


def test_stale_buttons_do_not_act_and_repeat_the_current_question(chat):
    chat.say("hi")
    chat.tap("svc:1")  # a service button from an old message while at the main menu
    assert "that option has expired" in chat.main.body and chat.state == "MAIN_MENU"

    chat.tap("menu:book")
    chat.tap("slot:2026-09-21T09:00")  # a slot from an old list while choosing a service
    assert chat.state == "BOOK_SELECT_SERVICE"
    assert chat.last[0].body.startswith("Sorry, I didn't understand")
    assert chat.ids == ["svc:1", "svc:2", "svc:3", "svc:4"]

    chat.tap("svc:99")  # a service that doesn't exist
    assert chat.state == "BOOK_SELECT_SERVICE"


def test_stale_slot_from_a_different_date_is_rejected(chat):
    chat.go_to_slots(date="date:2026-09-22")

    chat.tap("slot:2026-09-21T09:00")  # belongs to the Monday list, but we're looking at Tuesday

    assert chat.state == "BOOK_SELECT_SLOT"
    assert chat.last[0].body.startswith("Sorry, I didn't understand")


def test_typed_text_where_a_choice_is_expected_repeats_the_question(chat):
    chat.say("hi")
    chat.tap("menu:book")

    chat.say("root canal please")

    assert chat.state == "BOOK_SELECT_SERVICE"
    assert chat.ids == ["svc:1", "svc:2", "svc:3", "svc:4"]


@pytest.mark.parametrize("bad_name", ["R2D2", "A", "   ", "Robert'); DROP TABLE patients;--", "😀😀"])
def test_invalid_names_are_rejected_and_asked_again(chat, bad_name):
    chat.go_to_slots()
    chat.tap("slot:2026-09-21T09:00")

    chat.say(bad_name)

    assert chat.state == "BOOK_ENTER_NAME"
    assert "Please try again" in chat.last[0].body or "Please type" in chat.last[0].body


def test_a_button_while_a_name_is_expected_asks_for_the_name(chat):
    chat.go_to_slots()
    chat.tap("slot:2026-09-21T09:00")

    chat.tap("date:today")

    assert chat.state == "BOOK_ENTER_NAME"
    assert "Please type the patient's full name." in chat.last[0].body


@pytest.mark.parametrize("keyword", ["menu", "Menu", " HI ", "start"])
def test_keywords_return_to_the_main_menu_from_anywhere(chat, keyword):
    chat.go_to_slots()

    chat.say(keyword)

    assert chat.state == "MAIN_MENU"
    assert chat.ids == ["menu:book", "menu:view", "menu:manage"]


def test_idle_sessions_reset_to_the_main_menu(chat):
    chat.go_to_slots()
    assert chat.state == "BOOK_SELECT_SLOT"

    chat.now = NOW + timedelta(minutes=45)
    chat.tap("slot:2026-09-21T09:00")  # tapping an option from the abandoned conversation

    assert chat.state == "MAIN_MENU"
    assert "that option has expired" in chat.main.body


def test_sessions_within_the_timeout_continue(chat):
    chat.go_to_slots()
    chat.now = NOW + timedelta(minutes=10)

    chat.tap("slot:2026-09-21T09:00")

    assert chat.state == "BOOK_ENTER_NAME"


def test_unsupported_messages_get_a_polite_repeat(chat):
    chat.say("hi")
    chat.tap("menu:book")

    chat._send(kind="unsupported")

    assert "I can only read text messages and button taps" in chat.last[0].body
    assert chat.state == "BOOK_SELECT_SERVICE"


def test_corrupt_session_context_recovers_to_the_menu(chat, db):
    chat.go_to_slots()
    session = db.query(ConversationSession).one()
    session.context_json = "{}"  # state says BOOK_SELECT_SLOT but the context is gone
    db.commit()

    chat.tap("slot:2026-09-21T09:00")

    assert chat.state == "MAIN_MENU"


def test_appointment_that_disappears_mid_flow_is_handled(chat, db):
    booked = book_via_chat(chat)
    chat.tap("reschedule:1")
    chat.tap("date:2026-09-22")
    booking.cancel_appointment(db, booked.id, phone=PHONE_A, now=NOW)  # cancelled elsewhere (e.g. REST API)

    chat.tap("slot:2026-09-22T10:00")

    assert "no longer active" in chat.text
    assert chat.state == "MAIN_MENU"


def slot_times(db, day=MONDAY) -> list[str]:
    slots = availability.get_available_slots(
        db, doctor_id=DR_SHARMA, service_id=GENERAL_CONSULTATION, day=day, now=NOW
    )
    return [s.strftime("%H:%M") for s in slots]
