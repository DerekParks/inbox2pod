"""IMAP poller tests with a fake imap-tools mailbox (no network)."""

from email import message_from_bytes

import pytest

from inbox2pod.config import Config
from inbox2pod.ingest import imap_source
from inbox2pod.ingest.imap_source import ImapSource

RAW = (
    b"Message-ID: <imap-1@example.com>\r\n"
    b"Date: Tue, 10 Feb 2026 19:00:00 GMT\r\n"
    b"From: Money Stuff <news@dailyledger.example>\r\n"
    b"To: pod@example.com\r\n"
    b"Subject: Morning wrap\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n"
    b"<p>The market closed up eleven points.</p>"
)


class FakeMessage:
    def __init__(self, uid, raw=RAW):
        self.uid = uid
        self.obj = message_from_bytes(raw)


class FakeMailBox:
    """Stands in for imap_tools.MailBox. Tests set the class-level presets."""

    instances = []
    preset_folders = set()
    preset_messages = None
    fail_fetch = None
    fail_action = None

    def __init__(self, host, port=993, **kwargs):
        self.host = host
        self.port = port
        self.login_args = None
        self.folder = FakeFolderManager(self)
        self.folders = {"INBOX"} | set(FakeMailBox.preset_folders)
        self.messages = FakeMailBox.preset_messages if FakeMailBox.preset_messages is not None else [
            FakeMessage("1"),
            FakeMessage("2"),
        ]
        self.fail_fetch = FakeMailBox.fail_fetch
        self.fail_action = FakeMailBox.fail_action
        self.seen = []
        self.deleted = []
        self.moved = []
        self.created = []
        self.expunge_calls = 0
        self.fetch_kwargs = None
        FakeMailBox.instances.append(self)

    def login(self, username, password, folder=None):
        self.login_args = (username, password, folder)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def fetch(self, criteria, mark_seen=False, limit=None, **kwargs):
        self.fetch_kwargs = {"criteria": str(criteria), "mark_seen": mark_seen, "limit": limit}
        if self.fail_fetch:
            raise self.fail_fetch
        return iter(self.messages)

    def flag(self, uids, flag_set, value):
        self._guard_action()
        self.seen.append((list(uids), flag_set, value))

    def delete(self, uids):
        self._guard_action()
        self.deleted.append(list(uids))

    def expunge(self):
        self.expunge_calls += 1

    def move(self, uids, destination):
        self._guard_action()
        self.moved.append((list(uids), destination))

    def _guard_action(self):
        if self.fail_action:
            raise self.fail_action


class FakeFolderManager:
    def __init__(self, mailbox):
        self.mailbox = mailbox

    def exists(self, folder):
        return folder in self.mailbox.folders

    def create(self, folder):
        self.mailbox.folders.add(folder)
        self.mailbox.created.append(folder)


@pytest.fixture(autouse=True)
def reset_fake_mailbox():
    FakeMailBox.instances = []
    FakeMailBox.preset_folders = set()
    FakeMailBox.preset_messages = None
    FakeMailBox.fail_fetch = None
    FakeMailBox.fail_action = None
    yield
    FakeMailBox.instances = []
    FakeMailBox.preset_folders = set()
    FakeMailBox.preset_messages = None
    FakeMailBox.fail_fetch = None
    FakeMailBox.fail_action = None


@pytest.fixture
def patch_mailbox(monkeypatch):
    monkeypatch.setattr(imap_source, "MailBox", FakeMailBox)
    return FakeMailBox


@pytest.fixture
def source(patch_mailbox):
    return ImapSource(host="imap.test", port=993, username="pod", password="secret", folder="Newsletters")


def test_login_uses_the_configured_folder(source):
    source.fetch(on_message=lambda email: None)
    mailbox = FakeMailBox.instances[0]
    assert mailbox.login_args == ("pod", "secret", "Newsletters")
    assert mailbox.host == "imap.test"
    assert mailbox.port == 993


def test_fetch_returns_parsed_emails(source):
    emails = source.fetch(on_message=lambda email: None)
    assert len(emails) == 2
    assert emails[0].message_id == "imap-1@example.com"
    assert emails[0].subject == "Morning wrap"
    assert emails[0].sender_email == "news@dailyledger.example"
    assert emails[0].raw_bytes.startswith(b"Message-ID:")


def test_criteria_is_unseen_and_the_limit_is_passed(source):
    source.fetch(on_message=lambda email: None)
    kwargs = FakeMailBox.instances[0].fetch_kwargs
    assert "UNSEEN" in kwargs["criteria"].upper()
    assert kwargs["mark_seen"] is False
    assert kwargs["limit"] == Config.IMAP_MAX_EMAILS_PER_POLL


def test_max_emails_of_zero_means_no_limit(patch_mailbox):
    ImapSource(max_emails=0).fetch()
    assert FakeMailBox.instances[0].fetch_kwargs["limit"] is None


def test_mark_seen_flags_every_fetched_uid(source):
    source.fetch(on_message=lambda email: None)
    assert FakeMailBox.instances[0].seen == [(["1", "2"], "\\Seen", True)]


def test_delete_action_deletes_and_expunges(source):
    source.after_action = "delete"
    source.fetch(on_message=lambda email: None)
    mailbox = FakeMailBox.instances[0]
    assert mailbox.deleted == [["1", "2"]]
    assert mailbox.expunge_calls == 1
    assert mailbox.seen == []


def test_move_action_creates_a_missing_destination(source):
    source.after_action = "move:Processed"
    source.fetch(on_message=lambda email: None)
    mailbox = FakeMailBox.instances[0]
    assert mailbox.moved == [(["1", "2"], "Processed")]
    assert mailbox.created == ["Processed"]


def test_move_action_reuses_an_existing_destination(source):
    FakeMailBox.preset_folders = {"Processed"}
    source.after_action = "move:Processed"
    source.fetch(on_message=lambda email: None)
    mailbox = FakeMailBox.instances[0]
    assert mailbox.moved == [(["1", "2"], "Processed")]
    assert mailbox.created == []


def test_none_action_leaves_the_mailbox_alone(source):
    source.after_action = "none"
    source.fetch(on_message=lambda email: None)
    mailbox = FakeMailBox.instances[0]
    assert (mailbox.seen, mailbox.deleted, mailbox.moved) == ([], [], [])


def test_no_messages_means_no_after_action(source):
    FakeMailBox.preset_messages = []
    source.fetch(on_message=lambda email: None)
    assert FakeMailBox.instances[0].seen == []


def test_fetch_failure_propagates_to_the_caller(source):
    FakeMailBox.fail_fetch = OSError("connection reset")
    with pytest.raises(OSError):
        source.fetch(on_message=lambda email: None)


def test_after_action_failure_does_not_lose_emails(source, capsys):
    FakeMailBox.fail_action = OSError("server said no")
    emails = source.fetch(on_message=lambda email: None)
    assert len(emails) == 2
    assert "failed" in capsys.readouterr().out


def test_one_broken_message_does_not_stop_the_batch(source, monkeypatch):
    parse = imap_source.email_from_imap

    def explode(message):
        if message.uid == "1":
            raise ValueError("nope")
        return parse(message)

    monkeypatch.setattr(imap_source, "email_from_imap", explode)
    assert len(source.fetch(on_message=lambda email: None)) == 1


def test_source_defaults_come_from_config(patch_mailbox, monkeypatch):
    monkeypatch.setattr(Config, "IMAP_HOST", "imap.config.example")
    monkeypatch.setattr(Config, "IMAP_PORT", 1143)
    monkeypatch.setattr(Config, "IMAP_USER", "config-user")
    monkeypatch.setattr(Config, "IMAP_PASSWORD", "config-password")
    monkeypatch.setattr(Config, "IMAP_FOLDER", "Reading")
    monkeypatch.setattr(Config, "IMAP_AFTER_ACTION", "delete")

    source = ImapSource()
    assert (source.host, source.port, source.username, source.password) == (
        "imap.config.example",
        1143,
        "config-user",
        "config-password",
    )
    source.fetch(on_message=lambda email: None)
    assert FakeMailBox.instances[0].login_args == ("config-user", "config-password", "Reading")
    assert FakeMailBox.instances[0].deleted == [["1", "2"]]


def test_read_only_fetch_does_not_acknowledge(source):
    assert len(source.fetch()) == 2
    assert FakeMailBox.instances[0].seen == []


def test_processing_failure_is_not_acknowledged(source):
    FakeMailBox.preset_messages = [FakeMessage("1"), FakeMessage("2")]
    calls = []

    def persist(email):
        calls.append(email)
        if len(calls) == 1:
            raise OSError("disk full")
        assert FakeMailBox.instances[0].seen == []

    assert len(source.fetch(on_message=persist)) == 1
    assert FakeMailBox.instances[0].seen == [(["2"], "\\Seen", True)]


def test_parse_failure_is_not_deleted(source, monkeypatch):
    source.after_action = "delete"
    parse = imap_source.email_from_imap

    def parse_one(message):
        if message.uid == "1":
            raise ValueError("bad headers")
        return parse(message)

    monkeypatch.setattr(imap_source, "email_from_imap", parse_one)
    source.fetch(on_message=lambda email: None)
    assert FakeMailBox.instances[0].deleted == [["2"]]


def test_constructor_preserves_move_destination_case(patch_mailbox):
    source = ImapSource(after_action=" Move:Newsletters/Processed ")
    source.fetch(on_message=lambda email: None)
    assert FakeMailBox.instances[0].moved == [(["1", "2"], "Newsletters/Processed")]
