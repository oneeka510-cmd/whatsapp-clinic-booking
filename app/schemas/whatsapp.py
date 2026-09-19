"""Pydantic models for the parts of Meta's webhook payload we use.

Meta sends many event shapes (delivery statuses, template updates, ...). Everything is
optional/defaulted and unknown fields are ignored, so an unfamiliar event validates fine and
simply yields no messages.
"""

from pydantic import BaseModel, ConfigDict, Field


class WAText(BaseModel):
    body: str = ""


class WAReply(BaseModel):
    id: str
    title: str = ""


class WAInteractive(BaseModel):
    type: str = ""
    button_reply: WAReply | None = None
    list_reply: WAReply | None = None


class WAMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    id: str
    type: str = ""
    text: WAText | None = None
    interactive: WAInteractive | None = None


class WAValue(BaseModel):
    messages: list[WAMessage] = Field(default_factory=list)


class WAChange(BaseModel):
    field: str = ""
    value: WAValue = Field(default_factory=WAValue)


class WAEntry(BaseModel):
    changes: list[WAChange] = Field(default_factory=list)


class WAWebhookPayload(BaseModel):
    object: str = ""
    entry: list[WAEntry] = Field(default_factory=list)
