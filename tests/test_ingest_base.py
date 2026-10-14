"""Tests for the shared email parsing every ingest source uses."""

from datetime import UTC, datetime

from inbox2pod.ingest.base import IncomingEmail, parse_email


def build_raw(
    message_id="<one@example.com>",
    from_header="Money Stuff <news@dailyledger.example>",
    to_header="RECIPIENT@example.com",
    date="Tue, 10 Feb 2026 19:00:00 GMT",
    subject="The bond market is starting to say the quiet part",
    body="<p>hi</p>",
    extra_headers=(),
    omit=(),
) -> bytes:
    headers = [
        ("Message-ID", message_id),
        ("Date", date),
        ("From", from_header),
        ("To", to_header),
        ("Subject", subject),
        *extra_headers,
    ]
    lines = [f"{name}: {value}" for name, value in headers if name not in omit]
    return "\r\n".join(lines).encode() + b"\r\n\r\n" + body.encode()


def test_parses_every_field():
    email = parse_email(build_raw())
    assert email.message_id == "one@example.com"
    assert email.sender_name == "Money Stuff"
    assert email.sender_email == "news@dailyledger.example"
    assert email.recipient == "recipient@example.com"
    assert email.date == datetime(2026, 2, 10, 19, 0, tzinfo=UTC)
    assert email.subject == "The bond market is starting to say the quiet part"
    assert b"Subject:" in email.raw_bytes


def test_non_utc_date_becomes_utc():
    email = parse_email(build_raw(date="Tue, 10 Feb 2026 14:00:00 -0500"))
    assert email.date == datetime(2026, 2, 10, 19, 0, tzinfo=UTC)


def test_missing_date_is_none():
    assert parse_email(build_raw(omit=("Date",))).date is None


def test_garbage_date_is_none():
    assert parse_email(build_raw(date="whenever")).date is None


def test_missing_message_id_is_empty_string():
    assert parse_email(build_raw(omit=("Message-ID",))).message_id == ""


def test_encoded_subject_is_decoded():
    raw = b"Subject: =?utf-8?b?RG9sYyBleHRlbmRz?=\r\nFrom: a@b.example\r\n\r\n<p>hi</p>"
    assert parse_email(raw).subject == "Dolc extends"


def test_undecodable_subject_does_not_raise():
    email = parse_email(build_raw(subject="=?x-not-a-charset?Q?broken?="))
    assert isinstance(email.subject, str)


def test_envelope_recipient_wins_when_no_to_header():
    email = parse_email(build_raw(omit=("To",)), default_recipient="x7kq2m@pod.example.com")
    assert email.recipient == "x7kq2m@pod.example.com"


def test_delivered_to_header_beats_to_header():
    raw = build_raw(extra_headers=[("Delivered-To", "inbox2pod@pod.example.com")])
    assert parse_email(raw).recipient == "inbox2pod@pod.example.com"


def test_bare_from_header_has_no_display_name():
    email = parse_email(build_raw(from_header="news@dailyledger.example"))
    assert email.sender_name == ""
    assert email.sender_email == "news@dailyledger.example"


def test_empty_bytes_is_a_useless_but_harmless_email():
    email = parse_email(b"")
    assert email.message_id == ""
    assert email.subject == ""
    assert email.date is None


def test_incoming_email_is_constructible_by_hand():
    email = IncomingEmail(
        message_id="abc",
        subject="Hello",
        sender_name="Hello",
        sender_email="news@example.com",
        recipient="pod@example.com",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        raw_bytes=b"raw",
    )
    assert email.raw_bytes == b"raw"
