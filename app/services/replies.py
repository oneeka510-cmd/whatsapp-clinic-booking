"""Patient-facing message templates.

Pure formatting: every function takes already-loaded domain objects and returns a
channel-neutral message. No database access and no decisions live here.

Reply ids are `action:argument` strings (e.g. `svc:3`, `slot:2026-09-21T17:30`); the
conversation service is the only place that interprets them.
"""

import math
from datetime import date, datetime

from app.models import Appointment, Doctor, Service
from app.schemas.messages import (
    MAX_LIST_ROWS,
    Button,
    ButtonMessage,
    ListMessage,
    ListRow,
    TextMessage,
)
from app.utils.datetime_utils import format_date, format_date_full, format_date_short, format_time

FOOTER = 'Send "menu" anytime to start over'

# Leave room in a 10-row list for "earlier" / "more" navigation rows.
SLOT_PAGE_SIZE = MAX_LIST_ROWS - 2


def main_menu(clinic_name: str, notice: str | None = None) -> ButtonMessage:
    if notice:
        body = f"{notice}\n\nHow can we help you today?"
    else:
        body = f"👋 Welcome to {clinic_name}\n\nHow can we help you today?"
    return ButtonMessage(
        body=body,
        buttons=(
            Button("menu:book", "Book Appointment"),
            Button("menu:view", "View Appointment"),
            Button("menu:manage", "Manage Appointment"),
        ),
    )


def service_list(services: list[Service]) -> ListMessage:
    return ListMessage(
        body="Select a service",
        button_label="Select service",
        section_title="Services",
        rows=tuple(ListRow(f"svc:{s.id}", s.name, f"{s.duration_minutes} min") for s in services),
        footer=FOOTER,
    )


def doctor_list(doctors: list[Doctor], allow_any: bool) -> ListMessage:
    rows = [ListRow(f"doc:{d.id}", d.name, d.specialization) for d in doctors]
    if allow_any:
        rows.append(ListRow("doc:any", "Any Available Doctor", "First available doctor"))
    return ListMessage(
        body="Select your doctor",
        button_label="Select doctor",
        section_title="Doctors",
        rows=tuple(rows),
        footer=FOOTER,
    )


def date_choice(body: str) -> ButtonMessage:
    return ButtonMessage(
        body=body,
        buttons=(
            Button("date:today", "Today"),
            Button("date:tomorrow", "Tomorrow"),
            Button("date:other", "Choose another date"),
        ),
        footer=FOOTER,
    )


def date_list(dates: list[tuple[date, int]]) -> ListMessage:
    return ListMessage(
        body="Pick a date below, or type one (for example 25/09/2026).",
        button_label="Choose a date",
        section_title="Available dates",
        rows=tuple(
            ListRow(f"date:{day.isoformat()}", format_date_short(day), f"{count} slot{'s' if count != 1 else ''} available")
            for day, count in dates
        ),
        footer=FOOTER,
    )


def paginate_slots(slots: list[datetime], page: int) -> tuple[list[datetime], int, int]:
    """Split slots into list-sized pages. Returns (slots on this page, clamped page, page count)."""
    if len(slots) <= MAX_LIST_ROWS:
        return slots, 0, 1
    pages = math.ceil(len(slots) / SLOT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    return slots[page * SLOT_PAGE_SIZE : (page + 1) * SLOT_PAGE_SIZE], page, pages


def slot_list(day: date, page_slots: list[datetime], page: int, pages: int) -> ListMessage:
    rows: list[ListRow] = []
    if page > 0:
        rows.append(ListRow(f"slots:{page - 1}", "◀ Earlier times"))
    rows.extend(ListRow(f"slot:{slot.strftime('%Y-%m-%dT%H:%M')}", format_time(slot)) for slot in page_slots)
    if page < pages - 1:
        rows.append(ListRow(f"slots:{page + 1}", "More times ▶"))

    body = f"🕒 Available appointments\n{format_date_full(day)}"
    if pages > 1:
        body += f"\n(Page {page + 1} of {pages})"
    return ListMessage(
        body=body,
        button_label="Choose a time",
        section_title="Available times",
        rows=tuple(rows),
        footer=FOOTER,
    )


def ask_name() -> TextMessage:
    return TextMessage("📝 What is the patient's full name?\n\nPlease type it below.")


def _when(start: datetime) -> str:
    return f"{format_date(start.date())}\n{format_time(start)}"


def _what(appointment: Appointment) -> str:
    return f"{appointment.doctor.name}\n{appointment.service.name}"


def booking_summary(patient_name: str, service: Service, doctor: Doctor, start: datetime) -> ButtonMessage:
    return ButtonMessage(
        body=(
            "*Confirm Appointment*\n\n"
            f"Patient: {patient_name}\n"
            f"Service: {service.name}\n"
            f"Doctor: {doctor.name}\n"
            f"Date: {format_date(start.date())}\n"
            f"Time: {format_time(start)}"
        ),
        buttons=(Button("book:confirm", "Confirm"), Button("book:cancel", "Cancel")),
        footer=FOOTER,
    )


def booking_confirmed(appointment: Appointment, clinic_name: str) -> ButtonMessage:
    return ButtonMessage(
        body=(
            "✅ *Appointment Confirmed*\n\n"
            f"Booking ID: {appointment.booking_reference}\n\n"
            f"{_what(appointment)}\n\n"
            f"{_when(appointment.start_datetime)}\n\n"
            f"{clinic_name}\n\n"
            "Thank you!"
        ),
        buttons=(Button(f"manage:{appointment.id}", "Manage Appointment"), Button("menu:main", "Main Menu")),
    )


def appointment_details(appointment: Appointment) -> ButtonMessage:
    return ButtonMessage(
        body=(
            "*Your Upcoming Appointment*\n\n"
            f"Booking ID: {appointment.booking_reference}\n\n"
            f"{_what(appointment)}\n\n"
            f"{_when(appointment.start_datetime)}"
        ),
        buttons=(Button(f"manage:{appointment.id}", "Manage Appointment"), Button("menu:main", "Main Menu")),
    )


def appointment_list(appointments: list[Appointment], mode: str) -> ListMessage:
    question = "Which appointment would you like to view?" if mode == "view" else "Which appointment would you like to manage?"
    return ListMessage(
        body=question,
        button_label="Select booking",
        section_title="Your appointments",
        rows=tuple(
            ListRow(
                f"appt:{a.id}",
                f"{format_date_short(a.start_datetime.date())} · {format_time(a.start_datetime)}",
                f"{a.booking_reference} · {a.service.name} · {a.doctor.name}",
            )
            for a in appointments
        ),
        footer=FOOTER,
    )


def action_menu(appointment: Appointment) -> ButtonMessage:
    return ButtonMessage(
        body=(
            "*Manage Appointment*\n\n"
            f"Booking ID: {appointment.booking_reference}\n\n"
            f"{_what(appointment)}\n\n"
            f"{_when(appointment.start_datetime)}\n\n"
            "What would you like to do?"
        ),
        buttons=(
            Button(f"reschedule:{appointment.id}", "Reschedule"),
            Button(f"cancel:{appointment.id}", "Cancel Appointment"),
            Button("menu:main", "Main Menu"),
        ),
    )


def cancel_confirm(appointment: Appointment) -> ButtonMessage:
    return ButtonMessage(
        body=(
            "Are you sure you want to cancel?\n\n"
            f"Booking ID: {appointment.booking_reference}\n"
            f"{_what(appointment)}\n"
            f"{_when(appointment.start_datetime)}"
        ),
        buttons=(
            Button(f"cancel_confirm:{appointment.id}", "Yes, Cancel"),
            Button(f"cancel_keep:{appointment.id}", "Keep Appointment"),
        ),
    )


def cancelled(appointment: Appointment) -> ButtonMessage:
    return ButtonMessage(
        body=f"✅ *Appointment Cancelled*\n\nBooking ID: {appointment.booking_reference}",
        buttons=(Button("menu:book", "Book Appointment"), Button("menu:main", "Main Menu")),
    )


def reschedule_summary(appointment: Appointment, new_start: datetime) -> ButtonMessage:
    old = appointment.start_datetime
    return ButtonMessage(
        body=(
            "*Confirm Reschedule*\n\n"
            f"Booking ID: {appointment.booking_reference}\n"
            f"{_what(appointment)}\n\n"
            f"Current: {format_date(old.date())}, {format_time(old)}\n"
            f"New: {format_date(new_start.date())}, {format_time(new_start)}"
        ),
        buttons=(Button("reschedule_confirm", "Confirm"), Button("reschedule_abort", "Cancel")),
        footer=FOOTER,
    )


def rescheduled(appointment: Appointment, clinic_name: str) -> ButtonMessage:
    return ButtonMessage(
        body=(
            "✅ *Appointment Rescheduled*\n\n"
            f"Booking ID: {appointment.booking_reference}\n\n"
            f"{_what(appointment)}\n\n"
            f"{_when(appointment.start_datetime)}\n\n"
            f"{clinic_name}\n\n"
            "Thank you!"
        ),
        buttons=(Button(f"manage:{appointment.id}", "Manage Appointment"), Button("menu:main", "Main Menu")),
    )
