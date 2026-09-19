import json
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base
from app.utils.datetime_utils import now_local


class ConversationSession(Base):
    """Deterministic per-patient conversation state for the WhatsApp flow."""

    __tablename__ = "conversation_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(40), default="MAIN_MENU")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    @property
    def context(self) -> dict:
        try:
            value = json.loads(self.context_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @context.setter
    def context(self, value: dict) -> None:
        self.context_json = json.dumps(value)


class ProcessedMessage(Base):
    """WhatsApp message ids already handled. Meta retries webhooks, so this de-duplicates them."""

    __tablename__ = "processed_messages"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
