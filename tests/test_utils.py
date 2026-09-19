from datetime import date, datetime, timezone

import pytest

from app.utils.datetime_utils import (
    format_date,
    format_date_full,
    format_date_short,
    format_time,
    parse_user_date,
    to_clinic_naive,
)
from app.utils.phone_utils import mask_phone, normalize_phone


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+919876543210", "+919876543210"),
        ("919876543210", "+919876543210"),  # WhatsApp wa_id: digits with country code
        ("+91 98765 43210", "+919876543210"),
        ("+91-98765-43210", "+919876543210"),
        ("(+91) 98765 43210", "+919876543210"),
        ("9876543210", "+919876543210"),  # 10-digit national number gets the default country code
        ("09876543210", "+919876543210"),  # ...also with a leading trunk 0
        ("00919876543210", "+919876543210"),  # 00 international prefix
        ("+14155552671", "+14155552671"),  # other countries are left alone
        ("14155552671", "+14155552671"),
        ("  +919876543210  ", "+919876543210"),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_different_spellings_of_a_number_normalize_identically():
    variants = ["+91 98765 43210", "919876543210", "9876543210", "0091 9876543210", "+91-9876543210"]
    assert len({normalize_phone(v) for v in variants}) == 1


@pytest.mark.parametrize("raw", ["", "abc", "12345", "+", "+0123456789", "1" * 16, None, 9876543210])
def test_normalize_phone_rejects_garbage(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_a_wa_id_is_never_mistaken_for_a_national_number():
    """A 10-digit '+'-prefixed number (e.g. Iceland +354 123 4567) must not gain the default country code."""
    assert normalize_phone("+3541234567") == "+3541234567"


def test_mask_phone():
    assert mask_phone("+919876543210") == "+91*******210"
    assert mask_phone("+1234") == "***"


@pytest.mark.parametrize(
    "hour, minute, expected",
    [(0, 0, "12:00 AM"), (9, 5, "9:05 AM"), (12, 0, "12:00 PM"), (12, 30, "12:30 PM"), (14, 0, "2:00 PM"), (17, 30, "5:30 PM"), (23, 59, "11:59 PM")],
)
def test_format_time(hour, minute, expected):
    assert format_time(datetime(2026, 9, 21, hour, minute)) == expected


def test_date_formats():
    monday = date(2026, 9, 21)
    assert format_date(monday) == "21 September 2026"
    assert format_date_short(monday) == "Mon, 21 Sep"
    assert format_date_full(monday) == "Monday, 21 September 2026"
    assert format_date(date(2026, 1, 5)) == "5 January 2026"


@pytest.mark.parametrize(
    "text",
    ["2026-09-25", "25/09/2026", "25-09-2026", "25.09.2026", "25 Sep 2026", "25 September 2026", " 25  sep  2026 ", "25 SEPTEMBER 2026"],
)
def test_parse_user_date_accepts_common_formats(text):
    assert parse_user_date(text) == date(2026, 9, 25)


def test_parse_user_date_is_day_first():
    assert parse_user_date("03/04/2026") == date(2026, 4, 3)


@pytest.mark.parametrize("text", ["", "tomorrow", "31/02/2026", "2026-13-01", "25/09", "25 Foo 2026", "12345"])
def test_parse_user_date_rejects_everything_else(text):
    assert parse_user_date(text) is None


def test_to_clinic_naive_converts_aware_times_and_keeps_naive_ones():
    assert to_clinic_naive(datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc)) == datetime(2026, 9, 21, 10, 0)
    assert to_clinic_naive(datetime(2026, 9, 21, 10, 0)) == datetime(2026, 9, 21, 10, 0)
