"""Pipeline tests: ingest to episode row, and the TTS submit/poll/reconcile loop."""

import os
from datetime import UTC, datetime, timedelta

import pytest
import requests
from conftest import fixture_bytes

from inbox2pod.config import Config
from inbox2pod.episodes import Episode, EpisodeOps
from inbox2pod.ingest.base import IncomingEmail
from inbox2pod.ingest.imap_source import ImapSource
from inbox2pod.pipeline import (
    age_off_episodes,
    episode_id_for,
    episode_to_tts_text,
    ingest_new_emails,
    main_poll_loop,
    process_incoming,
    process_many,
    re_extract,
    reconcile_tts_state_on_boot,
    retention_cutoff,
    store_raw,
    submit_tts_request_for_episodes,
    tts_pending_and_completed_update,
)
from inbox2pod.tts_service import TTSService

TTS_ROOT = "http://tts.test"


def make_email(message_id="one@example.com", sender="news@dailyledger.example", subject="Hello", **kwargs) -> IncomingEmail:
    return IncomingEmail(
        message_id=message_id,
        subject=subject,
        sender_name=kwargs.get("sender_name", "Money Stuff"),
        sender_email=sender,
        recipient=kwargs.get("recipient", "pod@example.com"),
        date=kwargs.get("date", datetime(2026, 2, 10, 19, 0, tzinfo=UTC)),
        raw_bytes=kwargs.get("raw_bytes", fixture_bytes("money_stuff_longform.eml")),
    )


@pytest.fixture
def ops():
    return EpisodeOps()


@pytest.fixture
def episode(ops):
    episode_id = "ep-1"
    row = Episode(
        id=episode_id,
        feed="default",
        title="The bond market",
        description="A description",
        text="Markets did a thing. " * 30,
        sender_name="Money Stuff",
        sender_email="news@dailyledger.example",
        recipient="pod@example.com",
        published_at=datetime(2026, 2, 10, 19, 0),
        received_at=datetime(2026, 2, 10, 20, 0),
        raw_path="/tmp/ep-1.eml",
        extractor="trafilatura",
        extraction_ok=True,
    )
    ops.add_episode(row)
    return ops.get_episode(episode_id)


@pytest.fixture
def tts(monkeypatch, requests_mock, tmp_storage):
    """A TTS client pointed at a mock server, installed in the pipeline."""
    monkeypatch.setattr(Config, "TTS_ROOT_URL", TTS_ROOT)
    requests_mock.get(f"{TTS_ROOT}/health/check", json={"status": "healthy"})
    service = TTSService()
    monkeypatch.setattr("inbox2pod.pipeline.tts_service", service)
    return requests_mock


def test_episode_id_is_the_sha256_of_the_message_id():
    from inbox2pod.utils import sha256_hex

    assert episode_id_for(make_email(message_id="abc@example.com")) == sha256_hex("abc@example.com")


def test_episode_id_falls_back_to_from_date_subject():
    email = make_email(message_id="")
    assert episode_id_for(email) == episode_id_for(make_email(message_id=""))
    assert episode_id_for(email) != episode_id_for(make_email(message_id="", subject="Other"))
    assert episode_id_for(email) != episode_id_for(make_email(message_id="", sender="other@example.com"))


def test_episode_id_without_a_date_or_subject_still_works():
    assert len(episode_id_for(make_email(message_id="", date=None, subject=""))) == 64


def test_store_raw_writes_the_message(tmp_storage):
    raw_dir, _ = tmp_storage
    path = store_raw(b"raw bytes", "ep-9")
    assert os.path.exists(path)
    assert open(path, "rb").read() == b"raw bytes"
    assert os.path.dirname(path) == str(raw_dir)


def test_process_incoming_creates_an_episode(tmp_storage, ops):
    created = process_incoming(make_email())
    assert created is not None

    stored = ops.get_episode(created.id)
    assert stored.title == "Hello"
    assert stored.sender_name == "Money Stuff"
    assert stored.sender_email == "news@dailyledger.example"
    assert stored.recipient == "pod@example.com"
    assert stored.published_at == datetime(2026, 2, 10, 19, 0)
    assert stored.extraction_ok is True
    assert len(stored.text) > 200
    assert stored.extractor == "trafilatura"
    assert os.path.exists(stored.raw_path)
    assert stored.tts_job_id is None
    assert stored.mp3 is None


def test_process_incoming_is_idempotent(tmp_storage, ops):
    email = make_email()
    process_incoming(email)
    assert process_incoming(email) is None
    assert len(ops.get_episode_ids()) == 1


def test_process_incoming_drops_non_allowlisted_senders(tmp_storage, ops, monkeypatch, capsys):
    monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["good.example"])
    assert process_incoming(make_email(sender="spam@evil.example")) is None
    assert ops.get_episode_ids() == set()
    assert "not in SENDER_ALLOWLIST" in capsys.readouterr().out


def test_process_incoming_accepts_an_allowlisted_domain(tmp_storage, ops, monkeypatch):
    monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["dailyledger.example"])
    assert process_incoming(make_email()) is not None


def test_thin_extraction_is_flagged_not_synthesized(tmp_storage, ops):
    created = process_incoming(make_email(raw_bytes=fixture_bytes("tracking_pixel_only.eml")))
    assert created is not None
    assert created.extraction_ok is False

    pending = ops.get_episodes_to_tts()
    assert [ep.id for ep in pending] == []


def test_email_without_a_date_gets_received_time(tmp_storage, ops):
    created = process_incoming(make_email(date=None))
    assert created.published_at is not None
    assert abs(datetime.now(UTC).replace(tzinfo=None) - created.published_at) < timedelta(minutes=1)


def test_process_many_keeps_going_after_a_failure(tmp_storage, monkeypatch):
    real = process_incoming

    def flaky(email):
        if email.subject == "boom":
            raise RuntimeError("nope")
        return real(email)

    monkeypatch.setattr("inbox2pod.pipeline.process_incoming", flaky)
    created = process_many(
        [make_email(message_id="boom@x", subject="boom"), make_email(message_id="fine@x", subject="fine")]
    )
    assert [episode.title for episode in created] == ["fine"]


def test_process_many_returns_what_it_created(tmp_storage):
    created = process_many([make_email(message_id="a@x"), make_email(message_id="b@x")])
    assert len(created) == 2


def test_episode_to_tts_text_has_a_title_byline_and_body(episode):
    text = episode_to_tts_text(episode)
    assert text.startswith("The bond market\nWritten by Money Stuff\n\n")
    assert "Markets did a thing." in text


def test_episode_to_tts_text_truncates_the_body(episode):
    assert len(episode_to_tts_text(episode, max_length=10)) < 80


def test_episode_to_tts_text_without_a_sender_name(episode):
    episode.sender_name = ""
    assert "Written by" not in episode_to_tts_text(episode)


def test_submit_marks_the_episode(tmp_storage, tts, episode):
    tts.post(f"{TTS_ROOT}/tts/synthesize", json={"job_id": "job-1"})
    submit_tts_request_for_episodes([episode])

    from inbox2pod.pipeline import episode_ops

    assert episode_ops.get_episode("ep-1").tts_job_id == "job-1"
    body = tts.last_request.json()
    assert body["model"] == Config.TTS_MODEL
    assert body["voice"] == Config.TTS_VOICE
    assert "The bond market" in body["text"]


def test_submit_failure_leaves_the_episode_pending(tmp_storage, tts, episode):
    tts.post(f"{TTS_ROOT}/tts/synthesize", status_code=500)
    submit_tts_request_for_episodes([episode])

    from inbox2pod.pipeline import episode_ops

    assert episode_ops.get_episode("ep-1").tts_job_id is None


def test_download_completed_jobs_writes_the_mp3_and_deletes_the_job(tmp_storage, tts, episode):
    _, audio_dir = tmp_storage
    episode_ops = EpisodeOps()
    episode_ops.mark_tts_submitted("ep-1", "job-1")
    tts.get(f"{TTS_ROOT}/tts/jobs/job-1/download", content=b"fake mp3")
    tts.delete(f"{TTS_ROOT}/tts/jobs/job-1")

    from inbox2pod.pipeline import download_completed_tts_jobs

    download_completed_tts_jobs(["job-1"])

    assert episode_ops.get_episode("ep-1").mp3 == "job-1.mp3"
    assert (audio_dir / "job-1.mp3").exists()


def test_re_extract_updates_text_clears_tts_and_drops_the_mp3(tmp_storage, ops):
    _, audio_dir = tmp_storage
    created = process_incoming(make_email(raw_bytes=fixture_bytes("money_stuff_longform.eml")))
    ops.mark_tts_submitted(created.id, "job-1")
    ops.mark_tts_completed("job-1", "job-1.mp3")
    mp3 = audio_dir / "job-1.mp3"
    mp3.write_bytes(b"old audio")

    # pretend a bad extractor run left stale text behind
    ops.update_extraction(created.id, text="stale", description="stale", extractor="trafilatura", extraction_ok=True)

    result = re_extract(created.id)

    assert result.extractor == "trafilatura"
    fresh = ops.get_episode(created.id)
    assert len(fresh.text) > 200
    assert fresh.tts_job_id is None
    assert fresh.mp3 is None
    assert not mp3.exists()


def test_re_extract_without_a_raw_file(tmp_storage, ops, episode, capsys):
    assert re_extract("ep-1") is None
    assert "No raw message stored" in capsys.readouterr().out


def test_re_extract_unknown_episode(tmp_storage):
    assert re_extract("nope") is None


def test_retention_cutoff_is_none_when_disabled(episode, monkeypatch):
    monkeypatch.setattr(Config, "EPISODE_RETENTION_DAYS", None)
    assert retention_cutoff() is None


def test_age_off_removes_episodes_audio_and_raw_mail(tmp_storage, ops):
    _, audio_dir = tmp_storage
    created = process_incoming(make_email())
    mp3 = audio_dir / "job-1.mp3"
    mp3.write_bytes(b"audio")
    ops.mark_tts_submitted(created.id, "job-1")
    ops.mark_tts_completed("job-1", "job-1.mp3")

    age_off_episodes(datetime.now(UTC) + timedelta(days=1))

    assert ops.get_episode_ids() == set()
    assert not os.path.exists(created.raw_path)
    assert not mp3.exists()


def test_age_off_keeps_newer_episodes(tmp_storage, ops, monkeypatch):
    monkeypatch.setattr(Config, "EPISODE_RETENTION_DAYS", 30)
    created = process_incoming(make_email(date=datetime.now(UTC)))
    age_off_episodes(retention_cutoff())
    assert ops.get_episode(created.id) is not None


def test_boot_reconcile_skips_when_nothing_is_in_flight(tmp_storage, ops, capsys):
    reconcile_tts_state_on_boot()
    assert "no in-flight TTS jobs" in capsys.readouterr().out


def test_boot_reconcile_skips_when_tts_is_unreachable(tmp_storage, ops, episode, monkeypatch, capsys):
    ops.mark_tts_submitted("ep-1", "job-1")
    monkeypatch.setattr("inbox2pod.pipeline.tts_service.wake_and_wait_healthy", lambda: False)
    reconcile_tts_state_on_boot()
    assert "not reachable" in capsys.readouterr().out


def test_boot_reconcile_nulls_a_stale_job(tmp_storage, ops, episode, tts):
    ops.mark_tts_submitted("ep-1", "vanished-job")
    tts.get(f"{TTS_ROOT}/tts/jobs", json={"jobs": []})

    reconcile_tts_state_on_boot()

    assert ops.get_episode("ep-1").tts_job_id is None


def test_tts_update_downloads_a_completed_job(tmp_storage, ops, episode, tts):
    ops.mark_tts_submitted("ep-1", "job-1")
    tts.get(f"{TTS_ROOT}/tts/jobs", json={"jobs": [{"job_id": "job-1", "status": "COMPLETED"}]})
    tts.get(f"{TTS_ROOT}/tts/jobs/job-1/download", content=b"audio")
    tts.delete(f"{TTS_ROOT}/tts/jobs/job-1")

    tts_pending_and_completed_update()
    assert ops.get_episode("ep-1").mp3 == "job-1.mp3"


def test_main_poll_loop_skips_tts_when_there_is_no_work(tmp_storage, ops, monkeypatch, capsys):
    monkeypatch.setattr("inbox2pod.pipeline.ingest_new_emails", lambda: 0)
    monkeypatch.setattr(
        "inbox2pod.pipeline.tts_service.check_health", lambda: pytest.fail("should not have checked health")
    )
    assert main_poll_loop() == []
    assert "No pending or in-flight TTS work" in capsys.readouterr().out


def test_main_poll_loop_wakes_tts_and_submits(tmp_storage, ops, episode, tts, monkeypatch):
    monkeypatch.setattr("inbox2pod.pipeline.ingest_new_emails", lambda: 0)
    monkeypatch.setattr("inbox2pod.pipeline.tts_service.wake_and_wait_healthy", lambda: True)
    tts.get(f"{TTS_ROOT}/tts/jobs", json={"jobs": []})
    tts.post(f"{TTS_ROOT}/tts/synthesize", json={"job_id": "job-77"})

    submitted = main_poll_loop()

    assert [ep.id for ep in submitted] == ["ep-1"]
    assert ops.get_episode("ep-1").tts_job_id == "job-77"


def test_main_poll_loop_gives_up_when_tts_is_unhealthy(tmp_storage, ops, episode, monkeypatch, capsys):
    monkeypatch.setattr("inbox2pod.pipeline.ingest_new_emails", lambda: 0)
    monkeypatch.setattr("inbox2pod.pipeline.tts_service.wake_and_wait_healthy", lambda: False)

    assert main_poll_loop() == []
    assert "not healthy" in capsys.readouterr().out


def test_main_poll_loop_batches_submissions(tmp_storage, ops, episode, tts, monkeypatch):
    for i in range(4):
        ops.add_episode(
            Episode(
                id=f"batch-{i}",
                title=f"Episode {i}",
                text="body " * 60,
                published_at=datetime(2026, 2, 10, 19, 0),
                received_at=datetime(2026, 2, 10, 19, 0),
                raw_path="/tmp/x.eml",
                extraction_ok=True,
            )
        )
    monkeypatch.setattr(Config, "TTS_BATCH_SIZE", 2)
    monkeypatch.setattr("inbox2pod.pipeline.ingest_new_emails", lambda: 0)
    monkeypatch.setattr("inbox2pod.pipeline.tts_service.wake_and_wait_healthy", lambda: True)
    tts.get(f"{TTS_ROOT}/tts/jobs", json={"jobs": []})
    tts.post(f"{TTS_ROOT}/tts/synthesize", json={"job_id": "job-x"})

    assert len(main_poll_loop()) == 2


def test_ingest_new_emails_is_a_noop_without_imap(monkeypatch):
    monkeypatch.setattr(Config, "IMAP_HOST", None)
    assert ingest_new_emails() == 0


def test_ingest_new_emails_creates_episodes(tmp_storage, ops, monkeypatch):
    monkeypatch.setattr(ImapSource, "fetch", lambda self, on_message: [on_message(make_email(message_id=mid)) for mid in ("imap-1", "imap-2")])
    assert ingest_new_emails() == 2
    assert len(ops.get_episode_ids()) == 2


def test_ingest_new_emails_survives_an_imap_outage(monkeypatch, capsys):
    def explode(self, on_message):
        raise OSError("imap down")

    monkeypatch.setattr(ImapSource, "fetch", explode)
    assert ingest_new_emails() == 0
    assert "IMAP poll failed" in capsys.readouterr().out


def test_other_clients_tts_jobs_are_left_alone(tmp_storage, ops, episode, tts):
    from inbox2pod.pipeline import filter_job_ids_to_ones_we_know_about

    ops.mark_tts_submitted("ep-1", "job-known")

    assert filter_job_ids_to_ones_we_know_about(["job-known", "job-stranger"]) == ["job-known"]
    assert tts.request_history == []


def test_a_broken_episode_does_not_stop_the_batch(tmp_storage, ops, episode, tts, monkeypatch):
    monkeypatch.setattr("inbox2pod.pipeline.ingest_new_emails", lambda: 0)
    monkeypatch.setattr("inbox2pod.pipeline.tts_service.wake_and_wait_healthy", lambda: True)
    tts.get(f"{TTS_ROOT}/tts/jobs", json={"jobs": []})
    tts.post(f"{TTS_ROOT}/tts/synthesize", [
        {"status_code": 500},
        {"json": {"job_id": "job-ok"}},
    ])

    ops.add_episode(
        Episode(
            id="ep-2",
            title="Second",
            text="body " * 60,
            published_at=datetime(2026, 2, 11, 19, 0),
            received_at=datetime(2026, 2, 11, 19, 0),
            raw_path="/tmp/y.eml",
            extraction_ok=True,
        )
    )
    main_poll_loop()

    assert ops.get_episode("ep-1").tts_job_id is None
    assert ops.get_episode("ep-2").tts_job_id == "job-ok"


def test_feed_only_lists_synthesized_episodes(tmp_storage, ops, episode):
    assert [ep.id for ep in ops.get_episodes_with_mp3()] == []
    ops.mark_tts_submitted("ep-1", "job-1")
    ops.mark_tts_completed("job-1", "job-1.mp3")
    assert [ep.id for ep in ops.get_episodes_with_mp3()] == ["ep-1"]


def test_mark_tts_completed_for_an_unknown_job_is_harmless(ops):
    assert ops.mark_tts_completed("nobody-knows-me", "x.mp3") is False


def test_requests_errors_are_not_swallowed_by_the_client(tmp_storage, tts):
    from inbox2pod.pipeline import tts_service

    tts.get(f"{TTS_ROOT}/tts/jobs", status_code=500)
    with pytest.raises(requests.exceptions.HTTPError):
        tts_service.get_jobs()


@pytest.fixture
def file_database(monkeypatch, tmp_path):
    # An on-disk database lets worker threads share the same SQLite state.
    monkeypatch.setattr(Config, "DATABASE_URI", f"sqlite:///{tmp_path}/episodes.db")


def test_duplicate_delivery_preserves_original_raw(tmp_storage, ops, monkeypatch):
    from inbox2pod import pipeline

    email = make_email()
    extract = pipeline.extract_email

    def interleave(raw):
        monkeypatch.setattr(pipeline, "extract_email", extract)
        process_incoming(email)
        return extract(raw)

    monkeypatch.setattr(pipeline, "extract_email", interleave)
    process_incoming(email)
    stored = ops.get_episode(episode_id_for(email))
    assert open(stored.raw_path, "rb").read() == email.raw_bytes


def test_concurrent_deliveries_create_one_intact_episode(file_database, tmp_storage, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from inbox2pod import pipeline

    started, release, second_started = Event(), Event(), Event()
    extract = pipeline.extract_email

    def slow_extract(raw):
        started.set()
        assert release.wait(5)
        return extract(raw)

    def deliver_again():
        second_started.set()
        return process_incoming(make_email())

    monkeypatch.setattr(pipeline, "extract_email", slow_extract)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(process_incoming, make_email())
        try:
            assert started.wait(5)
            second = pool.submit(deliver_again)
            assert second_started.wait(5)
            assert not second.done()
        finally:
            release.set()
        created = first.result()
        assert second.result() is None
    assert open(created.raw_path, "rb").read() == make_email().raw_bytes
    assert len(EpisodeOps().get_episode_ids()) == 1


def test_overlapping_poll_cycles_submit_only_once(file_database, tmp_storage, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import Mock

    from inbox2pod import pipeline

    created = process_incoming(make_email())
    started, release, second_started = Event(), Event(), Event()

    def submit(text):
        started.set()
        assert release.wait(5)
        return "one-job"

    service = Mock()
    service.get_jobs.return_value = ([], ["one-job"])
    service.submit_tts.side_effect = submit
    monkeypatch.setattr(pipeline, "tts_service", service)
    monkeypatch.setattr(pipeline, "ingest_new_emails", lambda: 0)

    def poll_again():
        second_started.set()
        return main_poll_loop()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(main_poll_loop)
        try:
            assert started.wait(5)
            second = pool.submit(poll_again)
            assert second_started.wait(5)
            assert not second.done()
        finally:
            release.set()
        first.result()
        second.result()
    service.submit_tts.assert_called_once()
    assert EpisodeOps().get_episode(created.id).tts_job_id == "one-job"


def test_raw_storage_does_not_overwrite_existing_message(tmp_storage):
    path = store_raw(b"original", "same-id")
    assert store_raw(b"duplicate", "same-id") == path
    assert open(path, "rb").read() == b"original"
