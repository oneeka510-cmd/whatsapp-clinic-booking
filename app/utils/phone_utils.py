import re

from app.config import get_settings


def normalize_phone(raw: str, default_country_code: str | None = None) -> str:
    """Normalize a phone number to E.164 (`+919876543210`).

    - Numbers starting with `+` (or the `00` international prefix) are taken as international.
    - A bare 10-digit number (or 11 digits with a leading trunk `0`) gets the default country code.
    - Any other bare digit string is assumed to already include its country code, which is
      exactly what WhatsApp sends as `wa_id` (e.g. `919876543210`).

    Raises ValueError if the result is not a plausible E.164 number.
    """
    if not isinstance(raw, str):
        raise ValueError("Phone number must be a string")

    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    international = text.startswith("+")

    if not international and digits.startswith("00"):
        digits = digits[2:]
        international = True

    if not international:
        country_code = default_country_code or get_settings().default_country_code
        if len(digits) == 10:
            digits = country_code + digits
        elif len(digits) == 11 and digits.startswith("0"):
            digits = country_code + digits[1:]

    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise ValueError(f"Invalid phone number: {raw!r}")
    return "+" + digits


def mask_phone(phone: str) -> str:
    """+919876543210 -> +91*******210 (for logs)."""
    if len(phone) <= 6:
        return "***"
    return phone[:3] + "*" * (len(phone) - 6) + phone[-3:]
