import os

import pytest

os.environ.setdefault("DATABASE_URI", "sqlite://")

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


@pytest.fixture
def empty_db():
    from inbox2pod.episodes import Episode, Session

    with Session() as session:
        session.query(Episode).delete()
        session.commit()


@pytest.fixture
def tmp_storage(monkeypatch, tmp_path):
    """Point raw and mp3 storage at a temp directory."""
    from inbox2pod.config import Config

    raw_dir = tmp_path / "raw"
    audio_dir = tmp_path / "audio"
    monkeypatch.setattr(Config, "RAW_STORAGE_PATH", str(raw_dir))
    monkeypatch.setattr(Config, "MP3_STORAGE_PATH", str(audio_dir))
    return raw_dir, audio_dir
