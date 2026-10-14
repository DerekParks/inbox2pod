import os

import pytest

# Environment defaults have to be in place before inbox2pod is imported below.
os.environ.setdefault("DATABASE_URI", "sqlite://")
os.environ.setdefault("IMAP_HOST", "imap.test.example")
os.environ.setdefault("IMAP_USER", "pod@imap.test.example")
os.environ.setdefault("IMAP_PASSWORD", "test-password")
os.environ.setdefault("SMTP_ENABLED", "false")
os.environ.setdefault("FEED_TOKEN", "")
os.environ.setdefault("FEED_TITLE", "Test Inbox")

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture_names():
    return sorted(name for name in os.listdir(FIXTURES) if name.endswith(".eml"))


def fixture_bytes(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


@pytest.fixture
def raw(request):
    """Raw bytes of a fixture, for @pytest.mark.parametrize("raw", [...], indirect=True)."""
    return fixture_bytes(request.param)


@pytest.fixture(autouse=True)
def empty_db():
    """Give every test an empty episodes table."""
    from inbox2pod.episodes import Episode, make_session

    with make_session() as session:
        session.query(Episode).delete()
        session.commit()


@pytest.fixture
def tmp_storage(monkeypatch, tmp_path):
    """Point raw and mp3 storage at a temp directory."""
    from inbox2pod.config import Config  # noqa: PLC0415 - after the env defaults above

    raw_dir = tmp_path / "raw"
    audio_dir = tmp_path / "audio"
    raw_dir.mkdir()
    audio_dir.mkdir()
    monkeypatch.setattr(Config, "RAW_STORAGE_PATH", str(raw_dir))
    monkeypatch.setattr(Config, "MP3_STORAGE_PATH", str(audio_dir))
    return raw_dir, audio_dir


@pytest.fixture
def app_client(empty_db):
    """Test client with the schedulers and listeners kept off."""
    from unittest.mock import patch

    with (
        patch("apscheduler.schedulers.background.BackgroundScheduler.start"),
        patch("inbox2pod.ingest.smtp_source.SmtpReceiver.start"),
    ):
        from inbox2pod.api import app

        return app.test_client()
