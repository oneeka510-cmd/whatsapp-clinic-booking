"""Chat with the booking bot in your terminal.

Runs the exact conversation engine WhatsApp uses, against your local database, so you can try
the whole flow before (or without) setting up Meta. From the project root:

    python -m scripts.console_chat                  # chat as +919876543210
    python -m scripts.console_chat +919123456780    # chat as another patient

Type a number to tap a button / list row, or type any text. `quit` exits.
"""

import argparse
import sys

from app.database import database
from app.database.seed import seed_database
from app.schemas.messages import ButtonMessage, InboundMessage, ListMessage, OutboundMessage, TextMessage
from app.services.conversation_service import ConversationService
from app.utils.phone_utils import normalize_phone


def render(messages: list[OutboundMessage]) -> list[str]:
    """Print bot messages; returns the reply ids of the last interactive message (1-based menu)."""
    options: list[str] = []
    for message in messages:
        print()
        for line in message.body.splitlines() or [""]:
            print(f"  Bot | {line}")
        options = []
        if isinstance(message, ButtonMessage):
            for button in message.buttons:
                options.append(button.id)
                print(f"      | [{len(options)}] {button.title}")
        elif isinstance(message, ListMessage):
            for row in message.rows:
                options.append(row.id)
                extra = f"  ({row.description})" if row.description else ""
                print(f"      | [{len(options)}] {row.title}{extra}")
        elif isinstance(message, TextMessage):
            pass
    return options


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the clinic booking bot in the terminal.")
    parser.add_argument("phone", nargs="?", default="+919876543210", help="patient phone number")
    args = parser.parse_args()
    phone = normalize_phone(args.phone)

    # Redirected output on Windows defaults to a legacy code page that can't encode emoji;
    # degrade to "?" instead of crashing.
    sys.stdout.reconfigure(errors="replace")

    database.init_db()
    with database.SessionLocal() as db:
        seed_database(db)

    print(f"Chatting as {phone}. Type a number to tap an option, or any text. 'quit' exits.")
    options: list[str] = []
    counter = 0
    with database.SessionLocal() as db:
        first = ConversationService(db).handle(
            InboundMessage(phone=phone, message_id="console-0", kind="text", text="hi")
        )
        options = render(first)
        while True:
            try:
                typed = input("\n  You > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not sys.stdin.isatty():
                print(typed)  # piped input isn't echoed by a terminal; keep transcripts readable
            if typed.lower() in {"quit", "exit", "q"}:
                return
            counter += 1
            if typed.isdigit() and 1 <= int(typed) <= len(options):
                message = InboundMessage(
                    phone=phone, message_id=f"console-{counter}", kind="reply", reply_id=options[int(typed) - 1]
                )
            else:
                message = InboundMessage(phone=phone, message_id=f"console-{counter}", kind="text", text=typed)
            options = render(ConversationService(db).handle(message))


if __name__ == "__main__":
    sys.exit(main())
