"""Channel-neutral message types.

The conversation service consumes `InboundMessage` and produces `OutboundMessage`s;
`whatsapp_service` translates them to and from the Meta Cloud API JSON. Keeping this layer
free of WhatsApp JSON means the whole conversation can be tested (and driven from a console)
without Meta.
"""

from dataclasses import dataclass
from typing import Literal

# WhatsApp interactive-message limits that shape the UX (reply buttons: 3, list rows: 10).
MAX_BUTTONS = 3
MAX_LIST_ROWS = 10


@dataclass(frozen=True)
class Button:
    id: str
    title: str


@dataclass(frozen=True)
class ListRow:
    id: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class TextMessage:
    body: str


@dataclass(frozen=True)
class ButtonMessage:
    body: str
    buttons: tuple[Button, ...]
    footer: str = ""


@dataclass(frozen=True)
class ListMessage:
    body: str
    button_label: str
    rows: tuple[ListRow, ...]
    section_title: str = "Options"
    footer: str = ""


OutboundMessage = TextMessage | ButtonMessage | ListMessage


@dataclass(frozen=True)
class InboundMessage:
    phone: str  # normalized E.164 sender, e.g. +919876543210
    message_id: str
    kind: Literal["text", "reply", "unsupported"]
    text: str = ""  # for kind == "text"
    reply_id: str = ""  # for kind == "reply": id of the tapped button / list row
