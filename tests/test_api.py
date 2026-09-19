"""The complete booking lifecycle through the REST API (no WhatsApp involved).

These tests run against the real clock, so dates are computed relative to today.
"""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.database import get_db
from app.main import app
from app.utils.datetime_utils import now_local
from tests.conftest import PHONE_A, PHONE_B


def next_weekday(weekday: int, minimum_days_ahead: int = 2) -> date:
    day = now_local().date() + timedelta(days=minimum_days_ahead)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day


@pytest.fixture
def client(engine, db):  # `db` seeds the in-memory database
    factory = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def create(client, start: datetime, *, phone=PHONE_A, doctor_id=1, service_id=1, name="Rahul Sharma"):
    return client.post(
        "/appointments",
        json={
            "patient_name": name,
            "phone": phone,
            "doctor_id": doctor_id,
            "service_id": service_id,
            "start_datetime": start.isoformat(),
        },
    )


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_services_and_doctors(client):
    services = client.get("/services").json()
    assert [(s["id"], s["name"], s["duration_minutes"]) for s in services] == [
        (1, "General Consultation", 30),
        (2, "Teeth Cleaning", 30),
        (3, "Root Canal", 60),
        (4, "Orthodontic Consultation", 30),
    ]

    everyone = client.get("/doctors").json()
    assert [d["name"] for d in everyone] == ["Dr. Ananya Sharma", "Dr. Rohan Mehta", "Dr. Priya Kapoor"]

    root_canal_doctors = client.get("/doctors", params={"service_id": 3}).json()
    assert [d["name"] for d in root_canal_doctors] == ["Dr. Ananya Sharma", "Dr. Priya Kapoor"]
    assert client.get("/doctors", params={"service_id": 4}).json()[0]["name"] == "Dr. Rohan Mehta"


def test_availability_response_shape(client):
    monday = next_weekday(0)
    response = client.get("/availability", params={"doctor_id": 1, "service_id": 1, "date": monday.isoformat()})

    assert response.status_code == 200
    body = response.json()
    assert body["doctor_id"] == 1 and body["date"] == monday.isoformat()
    assert body["slots"] == [
        "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
        "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00", "17:30",
    ]  # fmt: skip


def test_closed_day_has_no_availability(client):
    sunday = next_weekday(6)
    response = client.get("/availability", params={"doctor_id": 1, "service_id": 1, "date": sunday.isoformat()})
    assert response.status_code == 200
    assert response.json()["slots"] == []


def test_availability_errors(client):
    monday = next_weekday(0).isoformat()
    assert client.get("/availability", params={"doctor_id": 99, "service_id": 1, "date": monday}).status_code == 404
    # Dr. Mehta (2) does not perform Root Canal (3)
    assert client.get("/availability", params={"doctor_id": 2, "service_id": 3, "date": monday}).status_code == 422
    assert client.get("/availability", params={"doctor_id": 1, "service_id": 1, "date": "not-a-date"}).status_code == 422


def test_full_booking_lifecycle(client):
    monday = next_weekday(0)
    tuesday = monday + timedelta(days=1)
    start = datetime.combine(monday, datetime.min.time()).replace(hour=17, minute=30)

    # 1. Book
    created = create(client, start, phone="+91 98765 43210")
    assert created.status_code == 201
    appointment = created.json()
    assert appointment["booking_reference"].startswith("AST-")
    assert appointment["status"] == "CONFIRMED"
    assert appointment["phone"] == PHONE_A  # normalized
    assert appointment["doctor"]["name"] == "Dr. Ananya Sharma"
    assert appointment["end_datetime"] == start.replace(minute=0, hour=18).isoformat()

    # 2. The slot is gone from availability
    def slots_on(day):
        params = {"doctor_id": 1, "service_id": 1, "date": day.isoformat()}
        return client.get("/availability", params=params).json()["slots"]

    assert "17:30" not in slots_on(monday)

    # 3. It shows up for the patient (and only for them)
    mine = client.get("/appointments", params={"phone": "9876543210"}).json()
    assert [a["id"] for a in mine] == [appointment["id"]]
    assert client.get("/appointments", params={"phone": PHONE_B}).json() == []

    # 4. Reschedule to Tuesday 10:00: old slot frees, new slot occupied
    moved = client.post(
        f"/appointments/{appointment['id']}/reschedule",
        json={"phone": PHONE_A, "new_start_datetime": f"{tuesday.isoformat()}T10:00:00"},
    )
    assert moved.status_code == 200
    assert moved.json()["start_datetime"] == f"{tuesday.isoformat()}T10:00:00"
    assert "17:30" in slots_on(monday)
    assert "10:00" not in slots_on(tuesday)

    # 5. Cancel: status changes, slot is free again, record is kept
    cancelled = client.post(f"/appointments/{appointment['id']}/cancel", json={"phone": PHONE_A})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert "10:00" in slots_on(tuesday)
    assert client.get("/appointments", params={"phone": PHONE_A}).json() == []
    history = client.get("/appointments", params={"phone": PHONE_A, "upcoming_only": False}).json()
    assert [a["status"] for a in history] == ["CANCELLED"]


def test_double_booking_returns_conflict(client):
    start = datetime.combine(next_weekday(0), datetime.min.time()).replace(hour=10)

    assert create(client, start).status_code == 201
    second = create(client, start, phone=PHONE_B, name="Someone Else")

    assert second.status_code == 409
    assert "no longer available" in second.json()["detail"]


def test_longer_service_overlap_returns_conflict(client):
    monday = next_weekday(0)
    assert create(client, datetime.combine(monday, datetime.min.time()).replace(hour=10, minute=30)).status_code == 201

    root_canal = create(
        client,
        datetime.combine(monday, datetime.min.time()).replace(hour=10),
        phone=PHONE_B,
        service_id=3,
        name="Someone Else",
    )
    assert root_canal.status_code == 409


def test_ownership_is_enforced_on_cancel_and_reschedule(client):
    start = datetime.combine(next_weekday(0), datetime.min.time()).replace(hour=10)
    appointment_id = create(client, start).json()["id"]

    cancel = client.post(f"/appointments/{appointment_id}/cancel", json={"phone": PHONE_B})
    assert cancel.status_code == 404

    new_start = (start + timedelta(hours=1)).isoformat()
    resched = client.post(
        f"/appointments/{appointment_id}/reschedule", json={"phone": PHONE_B, "new_start_datetime": new_start}
    )
    assert resched.status_code == 404

    still_there = client.get("/appointments", params={"phone": PHONE_A}).json()
    assert still_there[0]["status"] == "CONFIRMED"


def test_cancel_twice_conflicts(client):
    start = datetime.combine(next_weekday(0), datetime.min.time()).replace(hour=10)
    appointment_id = create(client, start).json()["id"]

    assert client.post(f"/appointments/{appointment_id}/cancel", json={"phone": PHONE_A}).status_code == 200
    assert client.post(f"/appointments/{appointment_id}/cancel", json={"phone": PHONE_A}).status_code == 409


def test_input_validation(client):
    start = datetime.combine(next_weekday(0), datetime.min.time()).replace(hour=10)

    assert create(client, start, phone="12345").status_code == 422  # not a phone number
    assert create(client, start, name="R2D2").status_code == 422  # digits in name
    assert create(client, start, name="X").status_code == 422  # too short
    assert create(client, start, doctor_id=99).status_code == 404
    assert create(client, start, doctor_id=2, service_id=3).status_code == 422  # Mehta: no root canals
    assert client.post("/appointments", json={"patient_name": "Rahul Sharma"}).status_code == 422
    assert client.get("/appointments").status_code == 422  # phone is required


def test_timezone_aware_datetimes_are_converted_to_clinic_time(client):
    monday = next_weekday(0)
    # A naive 04:30 is taken as clinic time, which is outside working hours.
    naive = create(client, datetime.combine(monday, datetime.min.time()).replace(hour=4, minute=30))
    assert naive.status_code == 409

    # 04:30 UTC is 10:00 in Asia/Kolkata (UTC+05:30), which is a valid slot.
    aware = client.post(
        "/appointments",
        json={
            "patient_name": "Rahul Sharma",
            "phone": PHONE_B,
            "doctor_id": 1,
            "service_id": 1,
            "start_datetime": f"{monday.isoformat()}T04:30:00Z",
        },
    )
    assert aware.status_code == 201
    assert aware.json()["start_datetime"] == f"{monday.isoformat()}T10:00:00"
