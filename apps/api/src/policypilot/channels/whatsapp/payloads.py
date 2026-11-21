"""WhatsApp Cloud API webhook payloads (docs/reference/verified-facts.md#whatsapp).

Only the fields PolicyPilot needs are modelled; unknown fields are ignored because Meta adds
fields over time. `from`/`wa_id` can be missing: the business-scoped `user_id` is preferred.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _Profile(_Loose):
    name: str | None = None


class _Contact(_Loose):
    wa_id: str | None = None
    user_id: str | None = None
    profile: _Profile | None = None


class _Text(_Loose):
    body: str = ""


class _ButtonReply(_Loose):
    id: str
    title: str = ""


class _Interactive(_Loose):
    type: str | None = None
    button_reply: _ButtonReply | None = None


class _Message(_Loose):
    id: str
    from_: str | None = Field(default=None, alias="from")
    from_user_id: str | None = None
    timestamp: str | None = None
    type: str = "unknown"
    text: _Text | None = None
    interactive: _Interactive | None = None


class _StatusError(_Loose):
    code: int | None = None


class _Status(_Loose):
    id: str
    status: str
    timestamp: str | None = None
    errors: list[_StatusError] = []


class _Value(_Loose):
    contacts: list[_Contact] = []
    messages: list[_Message] = []
    statuses: list[_Status] = []


class _Change(_Loose):
    field: str
    value: _Value


class _Entry(_Loose):
    changes: list[_Change] = []


class WebhookBody(_Loose):
    object: str | None = None
    entry: list[_Entry] = []


@dataclass(frozen=True)
class InboundMessage:
    provider_id: str
    user_key: str
    profile_name: str | None
    timestamp: int | None
    kind: str  # message | button | unsupported
    text: str
    button_id: str | None


@dataclass(frozen=True)
class StatusUpdate:
    provider_id: str
    status: str
    error_code: int | None


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """`X-Hub-Signature-256: sha256=<hex>`: HMAC-SHA256 of the raw body with the app secret."""
    if not header or not header.startswith("sha256=") or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def sign(app_secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def parse(body: dict[str, Any]) -> tuple[list[InboundMessage], list[StatusUpdate]]:
    parsed = WebhookBody.model_validate(body)
    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    for entry in parsed.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            contacts = change.value.contacts
            for index, message in enumerate(change.value.messages):
                contact = (
                    contacts[index]
                    if index < len(contacts)
                    else (contacts[0] if contacts else None)
                )
                user_key = (
                    (contact.user_id if contact else None)
                    or message.from_user_id
                    or (contact.wa_id if contact else None)
                    or message.from_
                )
                if not user_key:
                    continue
                if message.type == "text" and message.text is not None:
                    kind, text, button = "message", message.text.body, None
                elif (
                    message.type == "interactive"
                    and message.interactive
                    and message.interactive.button_reply
                ):
                    reply = message.interactive.button_reply
                    kind, text, button = "button", reply.title, reply.id
                else:
                    kind, text, button = "unsupported", "", None
                messages.append(
                    InboundMessage(
                        provider_id=message.id,
                        user_key=user_key,
                        profile_name=contact.profile.name if contact and contact.profile else None,
                        timestamp=int(message.timestamp) if message.timestamp else None,
                        kind=kind,
                        text=text,
                        button_id=button,
                    )
                )
            for status in change.value.statuses:
                statuses.append(
                    StatusUpdate(
                        provider_id=status.id,
                        status=status.status,
                        error_code=status.errors[0].code if status.errors else None,
                    )
                )
    return messages, statuses


def simulated_payload(
    *,
    user_id: str,
    name: str,
    message_id: str,
    text: str | None,
    button_id: str | None,
    timestamp: int,
    phone_number_id: str = "sim-phone-number",
) -> dict[str, Any]:
    """A Meta-shaped payload for the local simulator (docs/design/whatsapp.md)."""
    if button_id is not None:
        message: dict[str, Any] = {
            "from_user_id": user_id,
            "id": message_id,
            "timestamp": str(timestamp),
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": button_id, "title": text or button_id},
            },
        }
    else:
        message = {
            "from_user_id": user_id,
            "id": message_id,
            "timestamp": str(timestamp),
            "type": "text",
            "text": {"body": text or ""},
        }
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "sim-waba",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+1 555 0100",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [{"profile": {"name": name}, "user_id": user_id}],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }
