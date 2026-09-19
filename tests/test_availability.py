from datetime import date, timedelta

import pytest

from app.models import AppointmentStatus
from app.services import availability_service as availability
from app.services.errors import InvalidRequestError, NotFoundError
from tests.conftest import (
    DR_KAPOOR,
    DR_MEHTA,
    DR_SHARMA,
    GENERAL_CONSULTATION,
    MONDAY,
    NOW,
    ORTHO_CONSULTATION,
    ROOT_CANAL,
    SATURDAY,
    SUNDAY,
    at,
    hhmm,
    insert_appointment,
)

FULL_DAY_30_MIN = [
    "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
    "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00", "17:30",
]  # fmt: skip


def slots(db, *, day=MONDAY, doctor_id=DR_SHARMA, service_id=GENERAL_CONSULTATION, **kwargs):
    return hhmm(
        availability.get_available_slots(
            db, doctor_id=doctor_id, service_id=service_id, day=day, now=NOW, **kwargs
        )
    )


def test_doctor_schedule_generates_correct_slots(db):
    assert slots(db) == FULL_DAY_30_MIN


def test_booked_slots_disappear_from_availability(db):
    for time_ in ("10:30", "14:00", "16:30"):
        insert_appointment(db, start=at(MONDAY, time_))

    assert slots(db) == [
        "09:00", "09:30", "10:00", "11:00", "11:30", "12:00", "12:30",
        "14:30", "15:00", "15:30", "16:00", "17:00", "17:30",
    ]  # fmt: skip


def test_appointments_of_other_doctors_do_not_block(db):
    insert_appointment(db, doctor_id=DR_MEHTA, start=at(MONDAY, "10:30"))

    assert "10:30" in slots(db, doctor_id=DR_SHARMA)
    assert "10:30" not in slots(db, doctor_id=DR_MEHTA)


def test_cancelled_appointment_does_not_block(db):
    insert_appointment(db, start=at(MONDAY, "10:30"), status=AppointmentStatus.CANCELLED)

    assert slots(db) == FULL_DAY_30_MIN


def test_sixty_minute_service_cannot_overlap_a_thirty_minute_appointment(db):
    insert_appointment(db, start=at(MONDAY, "10:30"))  # occupies 10:30-11:00

    root_canal = slots(db, service_id=ROOT_CANAL)

    # 10:00-11:00 and 10:30-11:30 both overlap the 10:30 appointment.
    assert "10:00" not in root_canal
    assert "10:30" not in root_canal
    # 09:30-10:30 merely touches it, so it is fine.
    assert "09:30" in root_canal
    assert "11:00" in root_canal


def test_sixty_minute_service_must_fit_inside_a_schedule_block(db):
    root_canal = slots(db, service_id=ROOT_CANAL)

    # 12:30 + 60 min would run past the 13:00 end of the morning block; 17:30 past 18:00.
    assert root_canal == [
        "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00",
        "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00",
    ]  # fmt: skip


def test_long_existing_appointment_blocks_every_slot_it_overlaps(db):
    insert_appointment(db, start=at(MONDAY, "10:00"), minutes=60, service_id=ROOT_CANAL)  # 10:00-11:00

    thirty = slots(db)
    assert "10:00" not in thirty and "10:30" not in thirty
    assert "09:30" in thirty and "11:00" in thirty


def test_no_availability_on_a_closed_day(db):
    assert slots(db, day=SUNDAY) == []
    assert availability.get_slot_doctors(db, service_id=GENERAL_CONSULTATION, day=SUNDAY, now=NOW) == {}


def test_slots_already_started_are_not_offered(db):
    # NOW is Saturday 10:00, so 10:00 itself has started; 10:30 is the first bookable slot.
    saturday = slots(db, day=SATURDAY)
    assert saturday[0] == "10:30"
    assert "09:00" not in saturday and "10:00" not in saturday


def test_past_dates_and_dates_beyond_the_booking_window_have_no_slots(db):
    assert slots(db, day=SATURDAY - timedelta(days=1)) == []
    last_day = NOW.date() + timedelta(days=60)  # a Wednesday: the window's last bookable day
    assert last_day.weekday() == 2
    assert slots(db, day=last_day) != []
    assert slots(db, day=last_day + timedelta(days=1)) == []


def test_doctor_who_does_not_offer_the_service_is_rejected(db):
    with pytest.raises(InvalidRequestError):
        slots(db, doctor_id=DR_MEHTA, service_id=ROOT_CANAL)  # Dr. Mehta does no root canals


def test_unknown_doctor_or_service(db):
    with pytest.raises(NotFoundError):
        slots(db, doctor_id=999)
    with pytest.raises(NotFoundError):
        slots(db, service_id=999)


def test_inactive_doctor_is_not_bookable(db):
    from app.models import Doctor

    db.get(Doctor, DR_SHARMA).active = False
    db.commit()
    with pytest.raises(NotFoundError):
        slots(db)


def test_schedules_are_data_driven(db):
    """Changing a doctor's schedule in the database changes availability (nothing hardcoded)."""
    from app.models import DoctorSchedule

    for block in db.query(DoctorSchedule).filter_by(doctor_id=DR_SHARMA, day_of_week=MONDAY.weekday()):
        db.delete(block)
    db.commit()

    assert slots(db) == []
    assert slots(db, doctor_id=DR_MEHTA) == FULL_DAY_30_MIN


def test_is_slot_available_matches_the_slot_list(db):
    insert_appointment(db, start=at(MONDAY, "10:30"))

    kwargs = dict(doctor_id=DR_SHARMA, service_id=GENERAL_CONSULTATION, now=NOW)
    assert availability.is_slot_available(db, start=at(MONDAY, "10:00"), **kwargs)
    assert not availability.is_slot_available(db, start=at(MONDAY, "10:30"), **kwargs)
    assert not availability.is_slot_available(db, start=at(MONDAY, "13:00"), **kwargs)  # lunch break
    assert not availability.is_slot_available(db, start=at(MONDAY, "10:15"), **kwargs)  # off the grid


def test_excluding_an_appointment_frees_its_own_time(db):
    booked = insert_appointment(db, start=at(MONDAY, "10:30"))

    assert "10:30" not in slots(db)
    assert "10:30" in slots(db, exclude_appointment_id=booked.id)


def test_any_doctor_returns_the_union_with_doctor_ids(db):
    insert_appointment(db, doctor_id=DR_SHARMA, start=at(MONDAY, "09:00"))

    slot_map = availability.get_slot_doctors(db, service_id=GENERAL_CONSULTATION, day=MONDAY, now=NOW)

    # All three doctors do General Consultation; Sharma is busy at 09:00 so only two are free then.
    assert slot_map[at(MONDAY, "09:00")] == [DR_MEHTA, DR_KAPOOR]
    assert slot_map[at(MONDAY, "09:30")] == [DR_SHARMA, DR_MEHTA, DR_KAPOOR]
    assert list(slot_map) == sorted(slot_map)


def test_any_doctor_only_includes_doctors_who_offer_the_service(db):
    slot_map = availability.get_slot_doctors(db, service_id=ORTHO_CONSULTATION, day=MONDAY, now=NOW)
    assert {doc for docs in slot_map.values() for doc in docs} == {DR_MEHTA}


def test_bookable_dates_skip_closed_days_and_past_days(db):
    dates = availability.get_bookable_dates(
        db, service_id=GENERAL_CONSULTATION, doctor_id=DR_SHARMA, limit=4, now=NOW
    )

    assert [d for d, _ in dates] == [SATURDAY, MONDAY, MONDAY + timedelta(days=1), MONDAY + timedelta(days=2)]
    assert all(count > 0 for _, count in dates)
    assert date(2026, 9, 20) not in [d for d, _ in dates]  # Sunday


def test_fully_booked_day_is_not_a_bookable_date(db):
    for time_ in FULL_DAY_30_MIN:
        insert_appointment(db, start=at(MONDAY, time_))

    dates = availability.get_bookable_dates(
        db, service_id=GENERAL_CONSULTATION, doctor_id=DR_SHARMA, limit=3, now=NOW
    )
    assert MONDAY not in [d for d, _ in dates]
