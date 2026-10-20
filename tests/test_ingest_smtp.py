"""SMTP receiver tests: drive the aiosmtpd handler directly, no network."""

import asyncio
import smtplib
from email.message import EmailMessage

import pytest

from inbox2pod.config import Config
from inbox2pod.ingest import smtp_source
from inbox2pod.ingest.smtp_source import InboxHandler, SmtpReceiver, recipient_matches

RAW = (
    b"Message-ID: <smtp-1@newsletter.example>\r\n"
    b"Date: Tue, 10 Feb 2026 19:00:00 GMT\r\n"
    b"From: TLDR <daily@newsletter.example>\r\n"
    b"Subject: The daily digest\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Hello from the digest."
)


class FakeEnvelope:
    def __init__(self, mail_from="sender@example.com", rcpt_tos=None, content=RAW):
        self.mail_from = mail_from
        self.rcpt_tos = rcpt_tos if rcpt_tos is not None else []
        self.content = content


def run(handler, envelope, address="x7kq2m@pod.example.com"):
    """Run handle_RCPT then handle_DATA the way aiosmtpd would."""
    rcpt = asyncio.run(handler.handle_RCPT(None, None, envelope, address, []))
    if not rcpt.startswith("250"):
        return rcpt, None
    return rcpt, asyncio.run(handler.handle_DATA(None, None, envelope))


@pytest.fixture
def collected():
    return []


@pytest.fixture
def handler(collected):
    return InboxHandler(on_message=collected.append, allowed_recipients=["x7kq2m@pod.example.com"])


class TestRecipientMatching:
    def test_empty_allowlist_accepts_everything(self):
        assert recipient_matches("anything@example.com", [])

    def test_exact_address(self):
        assert recipient_matches("x7kq2m@pod.example.com", ["x7kq2m@pod.example.com"])

    def test_comparison_is_case_insensitive(self):
        assert recipient_matches("X7KQ2M@POD.EXAMPLE.COM", ["x7kq2m@pod.example.com"])

    def test_local_part_alone_matches(self):
        assert recipient_matches("x7kq2m@pod.example.com", ["x7kq2m"])

    def test_other_local_part_is_rejected(self):
        assert not recipient_matches("someone@example.com", ["x7kq2m@pod.example.com"])

    def test_other_domain_with_the_same_local_part_is_rejected(self):
        assert not recipient_matches("x7kq2m@evil.example", ["x7kq2m@pod.example.com"])

    def test_plus_address_is_a_different_local_part(self):
        assert not recipient_matches("x7kq2m+spam@pod.example.com", ["x7kq2m@pod.example.com"])


class TestHandlerRcpt:
    def test_accepts_the_secret_address(self, handler):
        envelope = FakeEnvelope()
        assert handler_rcpt(handler, envelope) == "250 OK"
        assert envelope.rcpt_tos == ["x7kq2m@pod.example.com"]

    def test_rejects_an_unknown_recipient(self, handler):
        assert handler_rcpt(handler, FakeEnvelope(), "random@example.com").startswith("550")

    def test_rejects_an_unknown_sender(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["newsletter.example"])
        handler = InboxHandler(on_message=lambda email: None, allowed_recipients=[])
        assert handler_rcpt(handler, FakeEnvelope(mail_from="spammer@evil.example")).startswith("550")

    def test_accepts_an_allowlisted_sender(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["newsletter.example"])
        handler = InboxHandler(on_message=lambda email: None, allowed_recipients=[])
        assert handler_rcpt(handler, FakeEnvelope(mail_from="daily@newsletter.example")) == "250 OK"

    def test_empty_sender_is_rejected_when_an_allowlist_is_set(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["newsletter.example"])
        handler = InboxHandler(on_message=lambda email: None, allowed_recipients=[])
        assert handler_rcpt(handler, FakeEnvelope(mail_from="")).startswith("550")


def handler_rcpt(handler, envelope, address="x7kq2m@pod.example.com"):
    return asyncio.run(handler.handle_RCPT(None, None, envelope, address, []))


class TestHandlerData:
    def test_queues_the_parsed_email(self, handler, collected):
        envelope = FakeEnvelope(rcpt_tos=["x7kq2m@pod.example.com"])
        _, response = run(handler, envelope)
        assert response.startswith("250")
        assert len(collected) == 1
        assert collected[0].message_id == "smtp-1@newsletter.example"
        assert collected[0].recipient == "x7kq2m@pod.example.com"

    def test_envelope_recipient_is_used_when_the_message_has_no_to_header(self, handler, collected):
        run(handler, FakeEnvelope(rcpt_tos=["secret@pod.example.com"]))
        assert collected[0].recipient == "secret@pod.example.com"

    def test_oversized_message_is_rejected(self, collected):
        handler = InboxHandler(on_message=collected.append, allowed_recipients=[], max_message_size=10)
        _, response = run(handler, FakeEnvelope(content=b"x" * 11))
        assert response.startswith("552")
        assert collected == []

    def test_handoff_failure_returns_a_tempfail(self):
        def explode(email):
            raise RuntimeError("disk on fire")

        handler = InboxHandler(on_message=explode, allowed_recipients=[])
        _, response = run(handler, FakeEnvelope())
        assert response.startswith("451")

    def test_unparseable_message_returns_a_tempfail(self, monkeypatch, collected):
        def explode(raw, default_recipient=None):
            raise ValueError("cannot parse")

        monkeypatch.setattr(smtp_source, "parse_email", explode)
        handler = InboxHandler(on_message=collected.append, allowed_recipients=[])
        _, response = run(handler, FakeEnvelope())
        assert response.startswith("451")
        assert collected == []


class TestSmtpReceiver:
    def test_worker_processes_the_queue(self, monkeypatch):
        processed = []
        monkeypatch.setattr(smtp_source, "InboxController", FakeController)
        receiver = SmtpReceiver(process=processed.append, port=2599)
        receiver.start()
        try:
            asyncio.run(receiver.handler.handle_DATA(None, None, FakeEnvelope(rcpt_tos=["a@pod.example.com"])))
        finally:
            receiver.stop()

        assert len(processed) == 1
        assert processed[0].message_id == "smtp-1@newsletter.example"

    def test_a_failing_processor_does_not_kill_the_worker(self, monkeypatch, capsys):
        calls = []

        def explode(email):
            calls.append(email)
            raise RuntimeError("nope")

        monkeypatch.setattr(smtp_source, "InboxController", FakeController)
        receiver = SmtpReceiver(process=explode, port=2598)
        receiver.start()
        try:
            for _ in range(2):
                asyncio.run(receiver.handler.handle_DATA(None, None, FakeEnvelope(rcpt_tos=["a@pod.example.com"])))
        finally:
            receiver.stop()

        assert len(calls) == 2
        assert "SMTP pipeline failed" in capsys.readouterr().out

    def test_stop_is_safe_before_start(self):
        SmtpReceiver(port=2597).stop()

    def test_running_flag(self, monkeypatch):
        monkeypatch.setattr(smtp_source, "InboxController", FakeController)
        receiver = SmtpReceiver(process=lambda email: None, port=2596)
        assert receiver.running is False
        receiver.start()
        assert receiver.running is True
        receiver.stop()
        assert receiver.running is False


class FakeController:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.stopped = False

    def start(self):
        return None

    def stop(self):
        self.stopped = True


@pytest.mark.parametrize("allowlist", [[], ["newsletter.example"]])
def test_end_to_end_over_a_real_socket(allowlist, monkeypatch):
    """A real SMTP conversation against the embedded listener on localhost."""
    received = []
    monkeypatch.setattr(Config, "SENDER_ALLOWLIST", allowlist)
    receiver = SmtpReceiver(
        process=received.append,
        host="127.0.0.1",
        port=2535,
        allowed_recipients=["x7kq2m@pod.example.com"],
    )
    receiver.start()
    try:
        message = EmailMessage()
        message["From"] = "daily@newsletter.example"
        message["To"] = "x7kq2m@pod.example.com"
        message["Subject"] = "Socket delivered"
        message.set_content("delivered over localhost")

        with smtplib.SMTP("127.0.0.1", 2535, timeout=10) as client:
            client.send_message(message)
    finally:
        receiver.stop()

    assert [email.subject for email in received] == ["Socket delivered"]
    assert received[0].recipient == "x7kq2m@pod.example.com"


def test_end_to_end_accepts_lines_longer_than_rfc_5321_allows():
    """Minified newsletter HTML puts thousands of characters on a single line."""
    received = []
    receiver = SmtpReceiver(
        process=received.append,
        host="127.0.0.1",
        port=2537,
        allowed_recipients=["x7kq2m@pod.example.com"],
    )
    receiver.start()
    try:
        raw = (
            b"From: daily@newsletter.example\r\n"
            b"To: x7kq2m@pod.example.com\r\n"
            b"Subject: Long lines\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            b"<div>" + b"x" * 50_000 + b"</div>"
        )
        with smtplib.SMTP("127.0.0.1", 2537, timeout=10) as client:
            client.sendmail("daily@newsletter.example", ["x7kq2m@pod.example.com"], raw)
    finally:
        receiver.stop()

    assert [email.subject for email in received] == ["Long lines"]


def test_end_to_end_a_tight_line_limit_is_enforced():
    received = []
    receiver = SmtpReceiver(
        process=received.append,
        host="127.0.0.1",
        port=2538,
        allowed_recipients=["x7kq2m@pod.example.com"],
        line_length_limit=1001,
    )
    receiver.start()
    try:
        raw = (
            b"From: daily@newsletter.example\r\n"
            b"To: x7kq2m@pod.example.com\r\n"
            b"Subject: Too long for this listener\r\n\r\n" + b"x" * 5000
        )
        with smtplib.SMTP("127.0.0.1", 2538, timeout=10) as client:
            with pytest.raises(smtplib.SMTPDataError) as refused:
                client.sendmail("daily@newsletter.example", ["x7kq2m@pod.example.com"], raw)
    finally:
        receiver.stop()

    assert refused.value.smtp_code == 500
    assert received == []


def test_end_to_end_rejects_the_wrong_address():
    received = []
    receiver = SmtpReceiver(
        process=received.append,
        host="127.0.0.1",
        port=2536,
        allowed_recipients=["x7kq2m@pod.example.com"],
    )
    receiver.start()
    try:
        with smtplib.SMTP("127.0.0.1", 2536, timeout=10) as client:
            with pytest.raises(smtplib.SMTPRecipientsRefused):
                client.sendmail("nobody@example.com", ["wrong@example.com"], "Subject: nope\r\n\r\nbody")
    finally:
        receiver.stop()

    assert received == []


def test_spool_survives_worker_failure_and_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(smtp_source, "InboxController", FakeController)

    def fail(email):
        raise OSError("database unavailable")

    receiver = SmtpReceiver(process=fail, spool_path=str(tmp_path))
    receiver.start()
    response = asyncio.run(receiver.handler.handle_DATA(None, None, FakeEnvelope(rcpt_tos=["secret@pod.example.com"])))
    receiver.stop()
    assert response.startswith("250")
    assert len(list(tmp_path.glob("*.json"))) == 1

    received = []
    recovered = SmtpReceiver(process=received.append, spool_path=str(tmp_path))
    recovered.start()
    recovered.stop()
    assert [email.raw_bytes for email in received] == [RAW]
    assert received[0].recipient == "secret@pod.example.com"
    assert list(tmp_path.glob("*.json")) == []


def test_spool_failure_tempfails_before_acknowledgement(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(smtp_source, "durable_write", fail)
    receiver = SmtpReceiver(spool_path=str(tmp_path))
    response = asyncio.run(receiver.handler.handle_DATA(None, None, FakeEnvelope()))
    assert response.startswith("451")
    assert receiver._handoff.empty()


def test_stop_keeps_running_true_until_worker_drains(monkeypatch, tmp_path):
    from threading import Event

    monkeypatch.setattr(smtp_source, "InboxController", FakeController)
    started, release = Event(), Event()

    def wait(email):
        started.set()
        assert release.wait(5)

    receiver = SmtpReceiver(process=wait, spool_path=str(tmp_path))
    receiver.start()
    try:
        receiver._enqueue(smtp_source.parse_email(RAW))
        assert started.wait(5)
        receiver.stop(timeout=0)
        assert receiver.running
    finally:
        release.set()
        receiver.stop()
    assert not receiver.running


def test_failed_spool_entry_is_retried_without_restart(monkeypatch, tmp_path):
    from threading import Event

    monkeypatch.setattr(smtp_source, "InboxController", FakeController)
    monkeypatch.setattr(smtp_source, "SPOOL_RETRY_SECONDS", 0.01)
    delivered = Event()
    calls = []

    def flaky(email):
        calls.append(email)
        if len(calls) == 1:
            raise OSError("temporarily unavailable")
        delivered.set()

    receiver = SmtpReceiver(process=flaky, spool_path=str(tmp_path))
    receiver.start()
    try:
        receiver._enqueue(smtp_source.parse_email(RAW))
        assert delivered.wait(5)
    finally:
        receiver.stop()
    assert len(calls) == 2
    assert list(tmp_path.glob("*.json")) == []
