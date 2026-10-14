"""
SMTP ingest: the power path.

An embedded aiosmtpd listener takes newsletter mail straight for a dedicated
address (subscribe with something@pod.example.com). Needs a domain, an MX
record and a reachable port 25, so it is opt-in.

The SMTP transaction only parses the envelope and queues the message; the
pipeline runs on a worker thread so the sending MTA is never held open.
"""

import re
from collections.abc import Callable
from queue import Queue
from threading import Thread

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP

from inbox2pod.config import Config
from inbox2pod.ingest.base import IncomingEmail, parse_email
from inbox2pod.pipeline import process_incoming

MAX_RCPT_LOG_PREVIEW = 200

# RFC 5321 caps a line at 1000 octets; minified newsletter HTML and unwrapped
# inline base64 blow straight past that
MAX_LINE_LENGTH = 65536


class LongLineSMTP(SMTP):
    """The aiosmtpd protocol with a 64 KiB line limit instead of 1 KiB."""

    line_length_limit = MAX_LINE_LENGTH

    def __init__(self, handler, *args, line_length_limit: int | None = None, **kwargs):
        if line_length_limit is not None:
            # SMTP.__init__ reads this to size its StreamReader, so set it first
            self.line_length_limit = line_length_limit
        super().__init__(handler, *args, **kwargs)


class InboxController(Controller):
    """Controller wired to LongLineSMTP."""

    def factory(self):
        return LongLineSMTP(self.handler, **self.SMTP_kwargs)

_RCPT_LOCAL_PART = re.compile(r"^[^@\s]+")


def recipient_matches(address: str, allowed: list[str]) -> bool:
    """Match an RCPT address against SMTP_ALLOWED_RCPT (full addresses or local parts)."""
    if not allowed:
        return True

    address = address.strip().lower()
    match = _RCPT_LOCAL_PART.match(address)
    local_part = match.group(0) if match else address
    for entry in allowed:
        entry = entry.strip().lower()
        if entry == address or entry == local_part or entry == f"@{local_part}":
            return True
    return False


class InboxHandler:
    """aiosmtpd handler that turns accepted messages into pipeline work."""

    def __init__(
        self,
        on_message: Callable[[IncomingEmail], None],
        allowed_recipients: list[str] | None = None,
        max_message_size: int | None = None,
        sender_allowed: Callable[[str], bool] | None = None,
    ):
        self.on_message = on_message
        self.allowed_recipients = allowed_recipients if allowed_recipients is not None else Config.SMTP_ALLOWED_RCPT
        self.max_message_size = max_message_size if max_message_size is not None else Config.SMTP_MAX_SIZE
        self.sender_allowed = sender_allowed or Config.sender_allowed

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        """Accept only the secret address(es), and only from allowlisted senders."""
        if not recipient_matches(address, self.allowed_recipients):
            print(f"SMTP rejected recipient {address}")
            return "550 Not accepting mail for this recipient"

        if not self.sender_allowed(envelope.mail_from or ""):
            print(f"SMTP rejected sender {envelope.mail_from!r}: not in SENDER_ALLOWLIST")
            return "550 Not accepting mail from this sender"

        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        """Queue the message; never do the heavy work inside the transaction."""
        content = envelope.content or b""
        if len(content) > self.max_message_size:
            print(f"SMTP rejected message over size limit: {len(content)} bytes")
            return "552 Message exceeds maximum size"

        recipients = [str(rcpt) for rcpt in envelope.rcpt_tos]
        try:
            email = parse_email(content, default_recipient=recipients[0] if recipients else None)
        except Exception as e:
            print(f"SMTP could not parse a {len(content)} byte message: {e}")
            return "451 Could not process this message"

        try:
            self.on_message(email)
        except Exception as e:
            print(f"SMTP handoff failed for {email.message_id or email.subject!r}: {e}")
            return "451 Could not process this message"

        print(f"SMTP accepted {email.subject[:MAX_RCPT_LOG_PREVIEW]!r} from {email.sender_email}")
        return "250 Message accepted for delivery"


class SmtpReceiver:
    """The listener: aiosmtpd on its own thread, pipeline on another."""

    def __init__(
        self,
        process: Callable[[IncomingEmail], None] | None = None,
        host: str | None = None,
        port: int | None = None,
        allowed_recipients: list[str] | None = None,
        max_message_size: int | None = None,
        line_length_limit: int | None = None,
    ):
        self.host = host or Config.SMTP_LISTEN_HOST
        self.port = port if port is not None else Config.SMTP_LISTEN_PORT
        self.max_message_size = max_message_size if max_message_size is not None else Config.SMTP_MAX_SIZE
        self.line_length_limit = line_length_limit if line_length_limit is not None else MAX_LINE_LENGTH
        self.allowed_recipients = (
            allowed_recipients if allowed_recipients is not None else Config.SMTP_ALLOWED_RCPT
        )
        self._process = process or process_incoming
        self._handoff: Queue = Queue()
        self._worker: Thread | None = None
        self._controller: InboxController | None = None
        self.handler = InboxHandler(
            on_message=self._handoff.put,
            allowed_recipients=self.allowed_recipients,
            max_message_size=self.max_message_size,
        )

    @property
    def running(self) -> bool:
        return self._controller is not None

    def start(self) -> None:
        """Start the worker and the SMTP listener."""
        self._worker = Thread(target=self._drain, name="inbox2pod-smtp-pipeline", daemon=True)
        self._worker.start()
        self._controller = InboxController(
            self.handler,
            hostname=self.host,
            port=self.port,
            data_size_limit=self.max_message_size,
            line_length_limit=self.line_length_limit,
        )
        self._controller.start()
        print(f"SMTP receiver listening on {self.host}:{self.port}")

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the listener, then let the worker finish what it already took."""
        if self._controller is not None:
            self._controller.stop()
            self._controller = None
        if self._worker is not None:
            self._handoff.put(None)
            self._worker.join(timeout=timeout)
            self._worker = None

    def _drain(self) -> None:
        while True:
            email = self._handoff.get()
            if email is None:
                self._handoff.task_done()
                return
            self._run_one(email)

    def _run_one(self, email: IncomingEmail | None) -> None:
        try:
            if email is not None:
                self._process(email)
        except Exception as e:
            print(f"SMTP pipeline failed: {e}")
        finally:
            self._handoff.task_done()
