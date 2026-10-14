"""Tests for the FlaskTtS client, including the wake-on-LAN path."""

from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from inbox2pod.config import Config
from inbox2pod.tts_service import TTSService


@pytest.fixture
def tts(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "TTS_ROOT_URL", "http://test-tts-service.com")
    monkeypatch.setattr(Config, "MP3_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(Config, "TTS_MODEL", "test-model")
    monkeypatch.setattr(Config, "TTS_VOICE", "test-voice")
    return TTSService()


def test_init_builds_the_endpoint_urls(tts, tmp_path):
    assert tts.root_url == "http://test-tts-service.com"
    assert tts.synthesize_path == "http://test-tts-service.com/tts/synthesize"
    assert tts.jobs_path == "http://test-tts-service.com/tts/jobs"
    assert tts.download_path == "http://test-tts-service.com/tts/jobs/{job_id}/download"
    assert tts.health_path == "http://test-tts-service.com/health/check"
    assert tts.mp3_storage_path == str(tmp_path)


def test_root_url_trailing_slash_is_stripped(monkeypatch):
    monkeypatch.setattr(Config, "TTS_ROOT_URL", "http://test-tts-service.com/")
    assert TTSService().root_url == "http://test-tts-service.com"


def test_audio_directory_is_created_when_the_first_mp3_lands(monkeypatch, tmp_path, requests_mock):
    target = tmp_path / "audio" / "nested"
    monkeypatch.setattr(Config, "MP3_STORAGE_PATH", str(target))
    service = TTSService()

    requests_mock.get(service.download_path.format(job_id="job-1"), content=b"audio bytes")
    saved = service.download_mp3("job-1")

    assert saved == str(target / "job-1.mp3")
    assert Path(saved).read_bytes() == b"audio bytes"


def test_submit_returns_the_job_id(requests_mock, tts):
    requests_mock.post(tts.synthesize_path, json={"job_id": "test-job-123"})

    assert tts.submit_tts("Test text") == "test-job-123"
    assert requests_mock.last_request.json() == {"text": "Test text", "model": "test-model", "voice": "test-voice"}


def test_submit_omits_an_empty_voice(requests_mock, tts, monkeypatch):
    monkeypatch.setattr(Config, "TTS_VOICE", "")
    requests_mock.post(tts.synthesize_path, json={"job_id": "job"})

    tts.submit_tts("Test text")
    assert "voice" not in requests_mock.last_request.json()


def test_submit_raises_on_error(requests_mock, tts):
    requests_mock.post(tts.synthesize_path, status_code=500)
    with pytest.raises(requests.exceptions.HTTPError):
        tts.submit_tts("Test text")


def test_get_jobs_splits_completed_from_ongoing(requests_mock, tts):
    requests_mock.get(
        tts.jobs_path,
        json={
            "jobs": [
                {"job_id": "job1", "status": "completed"},
                {"job_id": "job2", "status": "processing"},
                {"job_id": "job3", "status": "completed"},
            ]
        },
    )

    assert tts.get_jobs() == (["job1", "job3"], ["job2"])


def test_get_jobs_drops_failed_and_errored_jobs(requests_mock, tts):
    requests_mock.get(
        tts.jobs_path,
        json={
            "jobs": [
                {"job_id": "done", "status": "completed"},
                {"job_id": "running", "status": "running"},
                {"job_id": "pending", "status": "pending"},
                {"job_id": "broken", "status": "failed"},
                {"job_id": "busted", "status": "ERROR"},
            ]
        },
    )

    completed, ongoing = tts.get_jobs()
    assert completed == ["done"]
    assert ongoing == ["running", "pending"]


def test_get_jobs_raises_on_error(requests_mock, tts):
    requests_mock.get(tts.jobs_path, status_code=500)
    with pytest.raises(requests.exceptions.HTTPError):
        tts.get_jobs()


def test_download_mp3_writes_the_file(requests_mock, tts, tmp_path):
    requests_mock.get(tts.download_path.format(job_id="test-job-123"), content=b"fake mp3 content")

    saved_path = tts.download_mp3("test-job-123")

    assert saved_path == str(tmp_path / "test-job-123.mp3")
    with open(saved_path, "rb") as fh:
        assert fh.read() == b"fake mp3 content"


def test_download_mp3_raises_on_error(requests_mock, tts):
    requests_mock.get(tts.download_path.format(job_id="job"), status_code=404)
    with pytest.raises(requests.exceptions.HTTPError):
        tts.download_mp3("job")


def test_delete_job(requests_mock, tts):
    requests_mock.delete(f"{tts.jobs_path}/test-job-123")
    tts.delete_job("test-job-123")
    assert requests_mock.last_request.method == "DELETE"


def test_delete_job_raises_on_error(requests_mock, tts):
    requests_mock.delete(f"{tts.jobs_path}/test-job-123", status_code=500)
    with pytest.raises(requests.exceptions.HTTPError):
        tts.delete_job("test-job-123")


def test_check_health_true(requests_mock, tts):
    requests_mock.get(tts.health_path, status_code=200)
    assert tts.check_health() is True


def test_check_health_false_on_bad_status(requests_mock, tts):
    requests_mock.get(tts.health_path, status_code=500)
    assert tts.check_health() is False


def test_check_health_false_when_unreachable(requests_mock, tts):
    requests_mock.get(tts.health_path, exc=requests.exceptions.ConnectionError)
    assert tts.check_health() is False


def test_wake_and_wait_healthy_when_already_up(requests_mock, tts):
    requests_mock.get(tts.health_path, status_code=200)
    with patch("inbox2pod.tts_service.send_magic_packet") as magic_packet:
        assert tts.wake_and_wait_healthy() is True
        magic_packet.assert_not_called()


def test_wake_and_wait_healthy_does_nothing_when_wol_is_off(requests_mock, tts, monkeypatch):
    monkeypatch.setattr(Config, "TTS_WOL_ENABLED", False)
    requests_mock.get(tts.health_path, status_code=500)
    with patch("inbox2pod.tts_service.send_magic_packet") as magic_packet:
        assert tts.wake_and_wait_healthy() is False
        magic_packet.assert_not_called()


@pytest.fixture
def wol_enabled(monkeypatch):
    monkeypatch.setattr(Config, "TTS_WOL_ENABLED", True)
    monkeypatch.setattr(Config, "TTS_WOL_MAC", "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(Config, "TTS_WOL_BROADCAST", "255.255.255.255")
    monkeypatch.setattr(Config, "TTS_WOL_PORT", 9)
    monkeypatch.setattr(Config, "TTS_WOL_RETRY_DELAY", 0)


def test_wake_and_wait_healthy_wakes_the_host(requests_mock, tts, wol_enabled, monkeypatch):
    monkeypatch.setattr(Config, "TTS_WOL_MAX_RETRIES", 3)
    requests_mock.get(
        tts.health_path,
        [{"status_code": 500}, {"status_code": 500}, {"status_code": 200}],
    )

    with (
        patch("inbox2pod.tts_service.send_magic_packet") as magic_packet,
        patch("inbox2pod.tts_service.time.sleep") as sleep,
    ):
        assert tts.wake_and_wait_healthy() is True
        assert magic_packet.call_count == 2
        magic_packet.assert_called_with("aa:bb:cc:dd:ee:ff", ip_address="255.255.255.255", port=9)
        assert sleep.call_count == 2


def test_wake_and_wait_healthy_gives_up(requests_mock, tts, wol_enabled, monkeypatch):
    monkeypatch.setattr(Config, "TTS_WOL_MAX_RETRIES", 2)
    requests_mock.get(tts.health_path, status_code=500)

    with (
        patch("inbox2pod.tts_service.send_magic_packet") as magic_packet,
        patch("inbox2pod.tts_service.time.sleep"),
    ):
        assert tts.wake_and_wait_healthy() is False
        assert magic_packet.call_count == 2
