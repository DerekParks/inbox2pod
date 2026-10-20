"""
SMTP ingest: the power path.

An embedded aiosmtpd listener takes newsletter mail straight for a dedicated
address (subscribe with something@pod.example.com). Needs a domain, an MX
record and a reachable port 25, so it is opt-in.

The SMTP transaction only parses the envelope and queues the message; the
pipeline runs on a worker thread so the sending MTA is never held open.
"""

import base64
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from uuid import uuid4

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP

from inbox2pod.config import Config
from inbox2pod.ingest.base import IncomingEmail, parse_email
from inbox2pod.pipeline import process_incoming
from inbox2pod.storage import durable_mkdir, durable_write, sync_directory

MAX_RCPT_LOG_PREVIEW = 200

# RFC 5321 caps a line at 1000 octets; minified newsletter HTML and unwrapped
# inline base64 blow straight past that
MAX_LINE_LENGTH = 65536
SPOOL_RETRY_SECONDS = 30


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


def recipient_matches(address: str, allowed: list[str], delimiter: str = "+") -> bool:
    """Match an RCPT address against SMTP_ALLOWED_RCPT (full addresses or local parts).

    With a delimiter, ``secret<delimiter>tag@domain`` matches as ``secret@domain``.
    """
    if not allowed:
        return True

    address = address.strip().lower()
    match = _RCPT_LOCAL_PART.match(address)
    local_part = match.group(0) if match else address
    candidates = {address, local_part}
    if delimiter and delimiter in local_part:
        base = local_part.split(delimiter, 1)[0]
        if base:
            candidates |= {base, base + address[len(local_part) :]}
    for entry in allowed:
        entry = entry.strip().lower()
        if entry in candidates or entry == f"@{local_part}":
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
        recipient_delimiter: str | None = None,
    ):
        self.on_message = on_message
        self.allowed_recipients = allowed_recipients if allowed_recipients is not None else Config.SMTP_ALLOWED_RCPT
        self.max_message_size = max_message_size if max_message_size is not None else Config.SMTP_MAX_SIZE
        self.sender_allowed = sender_allowed or Config.sender_allowed
        self.recipient_delimiter = (
            recipient_delimiter if recipient_delimiter is not None else Config.SMTP_RCPT_DELIMITER
        )

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        """Accept only the secret address(es), and only from allowlisted senders."""
        if not recipient_matches(address, self.allowed_recipients, self.recipient_delimiter):
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
        spool_path: str | None = None,
    ):
        self.host = host or Config.SMTP_LISTEN_HOST
        self.port = port if port is not None else Config.SMTP_LISTEN_PORT
        self.max_message_size = max_message_size if max_message_size is not None else Config.SMTP_MAX_SIZE
        self.line_length_limit = line_length_limit if line_length_limit is not None else MAX_LINE_LENGTH
        self.allowed_recipients = (
            allowed_recipients if allowed_recipients is not None else Config.SMTP_ALLOWED_RCPT
        )
        self.spool_path = Path(spool_path or Config.SMTP_SPOOL_PATH)
        self._process = process or process_incoming
        self._handoff: Queue = Queue()
        self._worker: Thread | None = None
        self._controller: InboxController | None = None
        self._listener_stopped = False
        self._stop_requested = False
        self.handler = InboxHandler(
            on_message=self._enqueue,
            allowed_recipients=self.allowed_recipients,
            max_message_size=self.max_message_size,
        )

    @property
    def running(self) -> bool:
        return self._controller is not None

    def start(self) -> None:
        """Start the worker and the SMTP listener."""
        if self._controller is not None or (self._worker is not None and self._worker.is_alive()):
            raise RuntimeError("SMTP receiver is already running or draining")
        self._listener_stopped = False
        self._stop_requested = False
        durable_mkdir(self.spool_path)
        for path in sorted(self.spool_path.glob("*.json")):
            self._handoff.put(path)
        self._controller = InboxController(
            self.handler,
            hostname=self.host,
            port=self.port,
            data_size_limit=self.max_message_size,
            line_length_limit=self.line_length_limit,
        )
        self._worker = Thread(target=self._drain, name="inbox2pod-smtp-pipeline", daemon=True)
        self._worker.start()
        try:
            self._controller.start()
        except Exception:
            self.stop()
            raise
        print(f"SMTP receiver listening on {self.host}:{self.port}")

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the listener, then let the worker finish what it already took."""
        if self._controller is not None and not self._listener_stopped:
            self._controller.stop()
            self._listener_stopped = True
        if self._worker is not None:
            if not self._stop_requested:
                self._handoff.put(None)
                self._stop_requested = True
            self._worker.join(timeout=timeout)
        if self._worker is None or not self._worker.is_alive():
            self._controller = None
            self._worker = None

    def _enqueue(self, email: IncomingEmail) -> None:
        path = self.spool_path / f"{uuid4().hex}.json"
        payload = {"raw": base64.b64encode(email.raw_bytes).decode("ascii"), "recipient": email.recipient}
        durable_write(path, json.dumps(payload).encode("utf-8"))
        self._handoff.put(path)

    def _drain(self) -> None:
        failed: set[Path] = set()
        retry_at = time.monotonic() + SPOOL_RETRY_SECONDS
        while True:
            if time.monotonic() >= retry_at:
                for pending in sorted(failed):
                    self._handoff.put(pending)
                failed.clear()
                retry_at = time.monotonic() + SPOOL_RETRY_SECONDS
            try:
                path = self._handoff.get(timeout=max(0, retry_at - time.monotonic()))
            except Empty:
                continue
            try:
                if path is None:
                    return
                if not self._run_one(path):
                    failed.add(path)
            finally:
                self._handoff.task_done()

    def _run_one(self, path: Path) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            email = parse_email(base64.b64decode(payload["raw"]), default_recipient=payload["recipient"])
            self._process(email)
            path.unlink()
            sync_directory(self.spool_path)
            return True
        except Exception as e:
            print(f"SMTP pipeline failed for {path.name}; retained for retry: {e}")
            return False
