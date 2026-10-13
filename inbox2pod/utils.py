"""
Shared helpers
"""

import hashlib
import os
import re
from datetime import UTC, datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_path(path: str) -> str:
    """Resolve a possibly-relative configured path against the project root."""
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(PROJECT_ROOT, path))


def sha256_hex(value: str | bytes) -> str:
    """Hex sha256 of text or bytes."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def oxford_join(items: list[str]) -> str:
    """Join a list of items with commas and "and"."""
    items = [str(item) for item in items if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"

    return f"{', '.join(items[:-1])}, and {items[-1]}"


def to_utc(dt: datetime) -> datetime:
    """Attach UTC to a naive datetime, convert it if it is aware."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def utcnow_naive() -> datetime:
    """Current UTC time without tzinfo, the form SQLite columns store."""
    return datetime.now(UTC).replace(tzinfo=None)


_SUBJECT_CHROME = re.compile(
    r"""^\s*(?:
        re | fwd? | fw | aw | sv | vs |答复 | 轉寄 |转发
    )\s*[:：\-]\s*""",
    re.IGNORECASE | re.VERBOSE,
)
_LIST_TAG = re.compile(r"^\s*[\[\(][^\[\]\(\)]{1,60}[\]\)]\s*")


def clean_subject(subject: str | None) -> str:
    """Strip reply/forward prefixes and [list-tag] prefixes from a subject line.

    Tags are only stripped while they sit at the very front of the subject, so a
    title like "[Money Stuff] ..." becomes "Money Stuff: ..." style clean text.
    """
    if not subject:
        return ""

    text = " ".join(subject.split())
    previous = None
    while previous != text:
        previous = text
        text = _SUBJECT_CHROME.sub("", text)
        text = _LIST_TAG.sub("", text)

    return text.strip()


def sanitize_xml_string(text: str | None) -> str:
    """Sanitize a string to be XML compatible by removing invalid characters.

    XML 1.0 does not allow NULL bytes, control characters other than tab,
    newline and carriage return, or 0xFFFE/0xFFFF.
    """
    if text is None:
        return ""

    text = str(text).replace("\x00", "")

    result = []
    for char in text:
        code = ord(char)
        if code in (9, 10, 13) or (code >= 32 and code not in (0xFFFE, 0xFFFF)):
            result.append(char)
        elif code < 32:
            result.append(" ")

    return "".join(result)
