"""
The ingest source interface.

A source's only job is to hand fully-parsed `IncomingEmail` objects to the
pipeline. Sources never touch the database.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime
from typing import Protocol

from inbox2pod.utils import to_utc

_RECIPIENT_HEADERS = ("Envelope-To", "Delivered-To", "X-Original-To", "To")


@dataclass
class IncomingEmail:
    """One email as delivered by an ingest source."""

    message_id: str
    subject: str
    sender_name: str
    sender_email: str
    recipient: str
    date: datetime | None = None
    raw_bytes: bytes = field(repr=False, default=b"")


class EmailSource(Protocol):
    """Anything that can produce emails on demand."""

    def fetch(self) -> Iterator[IncomingEmail]:
        """Yield the emails that have not been handed off yet."""
        ...


def _header(msg, name: str) -> str:
    """Read a header defensively; malformed encoded words must not kill ingest."""
    try:
        value = msg.get(name)
        return "" if value is None else str(value).strip()
    except Exception:
        return ""


def _parse_email_date(raw: str) -> datetime | None:
    """Parse a Date header into UTC."""
    if not raw:
        return None
    try:
        return to_utc(parsedate_to_datetime(raw))
    except (TypeError, ValueError):
        return None


def parse_email(raw_bytes: bytes, default_recipient: str | None = None) -> IncomingEmail:
    """Parse a raw RFC822 message into an IncomingEmail.

    Args:
        raw_bytes: The whole message, headers included
        default_recipient: Recipient used when the message carries no To-style
            header, e.g. the SMTP envelope recipient
    """
    msg = message_from_bytes(raw_bytes, policy=policy.default)

    message_id = _header(msg, "Message-ID").strip("<> \t")
    sender_name, sender_email = parseaddr(_header(msg, "From"))

    recipient = default_recipient or ""
    if not recipient:
        for header in _RECIPIENT_HEADERS:
            _, address = parseaddr(_header(msg, header))
            if address:
                recipient = address
                break

    return IncomingEmail(
        message_id=message_id,
        subject=" ".join(_header(msg, "Subject").split()),
        sender_name=sender_name,
        sender_email=sender_email.lower(),
        recipient=recipient.lower(),
        date=_parse_email_date(_header(msg, "Date")),
        raw_bytes=raw_bytes,
    )
