"""
Configuration for the application
"""

import os
from urllib.parse import urlsplit

from inbox2pod.utils import resolve_path


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e


def _csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _lower_csv(name: str, default: str = "") -> list[str]:
    return [item.lower() for item in _csv(name, default)]


def imap_action(value: str) -> str:
    action, separator, folder = value.strip().partition(":")
    return action.lower() + (separator + folder.strip() if separator else "")


class Config:
    """Configuration for the application."""

    FLASK_ENV = os.getenv("FLASK_ENV")
    PORT = _int("PORT", "5003")

    # Ingest - IMAP poller (the default path)
    IMAP_HOST = os.getenv("IMAP_HOST")
    IMAP_PORT = _int("IMAP_PORT", "993")
    IMAP_USER = os.getenv("IMAP_USER")
    IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
    IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
    IMAP_POLL_MINUTES = _int("IMAP_POLL_MINUTES", "5")
    IMAP_MAX_EMAILS_PER_POLL = _int("IMAP_MAX_EMAILS_PER_POLL", "25")
    IMAP_AFTER_ACTION = imap_action(os.getenv("IMAP_AFTER_ACTION", "mark_seen"))

    # Ingest - SMTP receiver (the power path)
    SMTP_ENABLED = _bool("SMTP_ENABLED")
    SMTP_LISTEN_HOST = os.getenv("SMTP_LISTEN_HOST", "0.0.0.0")
    SMTP_LISTEN_PORT = _int("SMTP_LISTEN_PORT", "2525")
    SMTP_ALLOWED_RCPT = _lower_csv("SMTP_ALLOWED_RCPT")
    SMTP_RCPT_DELIMITER = os.getenv("SMTP_RCPT_DELIMITER", "+")
    SMTP_MAX_SIZE = _int("SMTP_MAX_SIZE", "10485760")

    # Applies to both sources. Empty accepts everything.
    SENDER_ALLOWLIST = _lower_csv("SENDER_ALLOWLIST")

    SMTP_SPOOL_PATH = resolve_path(os.getenv("SMTP_SPOOL_PATH", "data/smtp-spool"))

    # TTS
    TTS_ROOT_URL = os.getenv("TTS_ROOT_URL", "http://localhost:5001")
    TTS_MODEL = os.getenv("TTS_MODEL", "kokoro")
    TTS_VOICE = os.getenv("TTS_VOICE", "af_heart")
    TTS_CONNECT_TIMEOUT = _int("TTS_CONNECT_TIMEOUT", "5")
    TTS_READ_TIMEOUT = _int("TTS_READ_TIMEOUT", "120")
    TTS_BATCH_SIZE = _int("TTS_BATCH_SIZE", "10")

    TTS_WOL_ENABLED = _bool("TTS_WOL_ENABLED")
    TTS_WOL_MAC = os.getenv("TTS_WOL_MAC", "")
    TTS_WOL_BROADCAST = os.getenv("TTS_WOL_BROADCAST", "255.255.255.255")
    TTS_WOL_PORT = _int("TTS_WOL_PORT", "9")
    TTS_WOL_RETRY_DELAY = _int("TTS_WOL_RETRY_DELAY", "30")
    TTS_WOL_MAX_RETRIES = _int("TTS_WOL_MAX_RETRIES", "6")

    # Storage
    DATABASE_URI = os.getenv("DATABASE_URI", "sqlite:///data/inbox2pod.db")
    RAW_STORAGE_PATH = resolve_path(os.getenv("RAW_STORAGE_PATH", "data/raw"))
    MP3_STORAGE_PATH = resolve_path(os.getenv("MP3_STORAGE_PATH", "data/audio"))

    # Feed
    FEED_TITLE = os.getenv("FEED_TITLE", "Inbox2Pod")
    FEED_DESCRIPTION = os.getenv("FEED_DESCRIPTION", "My newsletters, read aloud.")
    FEED_AUTHOR = os.getenv("FEED_AUTHOR", "Inbox2Pod")
    FEED_OWNER_EMAIL = os.getenv("FEED_OWNER_EMAIL", "")
    FEED_LANGUAGE = os.getenv("FEED_LANGUAGE", "en-us")
    FEED_BASE_URL = os.getenv("FEED_BASE_URL", "").strip().rstrip("/")
    FEED_SITE_URL = os.getenv("FEED_SITE_URL", "")
    FEED_TOKEN = os.getenv("FEED_TOKEN", "").strip()
    FEED_MAX_EPISODES = _int("FEED_MAX_EPISODES", "1000")

    # Extraction
    MIN_TEXT_LENGTH = _int("MIN_TEXT_LENGTH", "200")
    DESCRIPTION_LENGTH = _int("DESCRIPTION_LENGTH", "300")

    EPISODE_RETENTION_DAYS = os.getenv("EPISODE_RETENTION_DAYS")
    EPISODE_RETENTION_DAYS = int(EPISODE_RETENTION_DAYS) if EPISODE_RETENTION_DAYS else None

    @classmethod
    def validate(cls) -> None:
        """Fail fast on unusable configuration."""
        if cls.FEED_BASE_URL:
            url = urlsplit(cls.FEED_BASE_URL)
            assert url.scheme in ("http", "https") and url.netloc and not url.path and not url.query and not url.fragment, (
                "FEED_BASE_URL must be an http(s) origin without a path, query, or fragment"
            )
        assert cls.TTS_CONNECT_TIMEOUT > 0 and cls.TTS_READ_TIMEOUT > 0, "TTS timeouts must be positive"
        if cls.TTS_WOL_ENABLED:
            assert cls.TTS_WOL_MAC, "TTS_WOL_MAC must be set when TTS_WOL_ENABLED is true"

        assert cls.IMAP_AFTER_ACTION in ("mark_seen", "none", "delete") or cls.IMAP_AFTER_ACTION.startswith("move:"), (
            f"IMAP_AFTER_ACTION must be mark_seen, none, delete or move:<folder>, got {cls.IMAP_AFTER_ACTION!r}"
        )

        assert not any(c == "@" or c.isspace() for c in cls.SMTP_RCPT_DELIMITER), (
            f"SMTP_RCPT_DELIMITER must not contain '@' or whitespace, got {cls.SMTP_RCPT_DELIMITER!r}"
        )

        assert cls.IMAP_HOST or cls.SMTP_ENABLED, (
            "No ingest source configured. Set IMAP_HOST/IMAP_USER/IMAP_PASSWORD for the IMAP poller, "
            "or SMTP_ENABLED=true to listen for SMTP deliveries."
        )
        if cls.IMAP_HOST:
            assert cls.IMAP_USER and cls.IMAP_PASSWORD, (
                f"IMAP_HOST is set to {cls.IMAP_HOST} but IMAP_USER/IMAP_PASSWORD is missing"
            )

    @classmethod
    def imap_enabled(cls) -> bool:
        """Whether the IMAP poller has enough config to run."""
        return bool(cls.IMAP_HOST and cls.IMAP_USER and cls.IMAP_PASSWORD)

    @classmethod
    def sender_allowed(cls, sender_email: str | None) -> bool:
        """Match a sender against SENDER_ALLOWLIST (addresses or bare domains)."""
        if not cls.SENDER_ALLOWLIST:
            return True
        if not sender_email:
            return False

        sender = sender_email.strip().lower()
        sender_domain = sender.rsplit("@", 1)[-1]
        for raw_entry in cls.SENDER_ALLOWLIST:
            entry = raw_entry.strip().lower()
            if entry.startswith("@"):
                if sender_domain == entry[1:]:
                    return True
            elif "@" in entry:
                if sender == entry:
                    return True
            elif sender_domain == entry or sender_domain.endswith(f".{entry}"):
                return True
        return False
