"""
Orchestration: ingest -> extract -> persist, then submit/poll TTS.

SQLite is the queue: an episode with a NULL tts_job_id is waiting to be sent
to FlaskTtS, an episode with a NULL mp3 has not been synthesized yet.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from inbox2pod.config import Config
from inbox2pod.episodes import Episode, EpisodeOps
from inbox2pod.extract import ExtractionResult, extract_email
from inbox2pod.ingest.base import IncomingEmail
from inbox2pod.ingest.imap_source import ImapSource
from inbox2pod.locking import serialized
from inbox2pod.storage import durable_write
from inbox2pod.tts_service import TTSService
from inbox2pod.utils import sha256_hex, utcnow_naive

tts_service = TTSService()
episode_ops = EpisodeOps()


def episode_id_for(email: IncomingEmail) -> str:
    """Stable id for an email: sha256 of Message-ID, or of From+Date+Subject.

    Makes IMAP re-polls, overlapping IMAP and SMTP delivery, and newsletter
    re-sends idempotent.
    """
    if email.message_id:
        return sha256_hex(email.message_id)

    date = email.date.isoformat() if email.date else ""
    return sha256_hex(f"{email.sender_email}|{date}|{email.subject}")


def store_raw(raw_bytes: bytes, episode_id: str) -> str:
    """Write the raw message to disk so it can always be re-extracted later."""
    path = os.path.join(Config.RAW_STORAGE_PATH, f"{episode_id}.eml")
    durable_write(Path(path), raw_bytes)
    return path


def episode_from(email: IncomingEmail, episode_id: str, raw_path: str, extraction: ExtractionResult) -> Episode:
    """Build the Episode row for an ingested email."""
    published_at = email.date.replace(tzinfo=None) if email.date else utcnow_naive()
    return Episode(
        id=episode_id,
        feed="default",
        title=email.subject or "(no subject)",
        description=extraction.description,
        text=extraction.text,
        sender_name=email.sender_name,
        sender_email=email.sender_email,
        recipient=email.recipient,
        published_at=published_at,
        received_at=utcnow_naive(),
        raw_path=raw_path,
        extractor=extraction.extractor,
        extraction_ok=extraction.ok,
    )


@serialized
def process_incoming(email: IncomingEmail) -> Episode | None:
    """Turn one ingested email into an episode.

    Returns the new Episode, or None when the sender is not allowlisted or the
    email has already been seen.
    """
    if not Config.sender_allowed(email.sender_email):
        print(f"Dropping mail from {email.sender_email or 'unknown sender'}: not in SENDER_ALLOWLIST")
        return None

    episode_id = episode_id_for(email)
    if episode_ops.episode_exists(episode_id):
        return None

    raw_path = store_raw(email.raw_bytes, episode_id)
    extraction = extract_email(email.raw_bytes)
    episode = episode_from(email, episode_id, raw_path, extraction)

    if not extraction.ok:
        print(f"Extraction too thin for {episode_id} ({len(extraction.text)} chars): flagged for review")

    if not episode_ops.add_episode(episode):
        return None

    return episode_ops.get_episode(episode_id)


def process_many(emails) -> list[Episode]:
    """Process a batch of emails, keeping going when one of them blows up."""
    created = []
    for email in emails:
        try:
            episode = process_incoming(email)
        except Exception as e:
            print(f"Failed to process incoming email: {e}")
            continue
        if episode:
            created.append(episode)

    return created


@serialized
def re_extract(episode_id: str) -> ExtractionResult | None:
    """Re-run extraction from the stored raw email and queue a fresh TTS run.

    Returns the new ExtractionResult, or None when there is no such episode or
    no stored raw message.
    """
    episode = episode_ops.get_episode(episode_id)
    if episode is None:
        return None
    if not episode.raw_path or not os.path.exists(episode.raw_path):
        print(f"No raw message stored for {episode_id}")
        return None

    with open(episode.raw_path, "rb") as fh:
        extraction = extract_email(fh.read())

    episode_ops.update_extraction(
        episode_id,
        text=extraction.text,
        description=extraction.description,
        extractor=extraction.extractor,
        extraction_ok=extraction.ok,
    )
    delete_mp3(episode_ops.get_episode_mp3(episode_id))
    episode_ops.clear_tts(episode_id)
    return extraction


def delete_mp3(mp3: str | None) -> None:
    """Remove a stored mp3 by bare filename or path."""
    if not mp3:
        return
    path = mp3 if os.path.isabs(mp3) else os.path.join(Config.MP3_STORAGE_PATH, mp3)
    if os.path.exists(path):
        os.remove(path)


def episode_to_tts_text(episode: Episode, max_length: int | None = None) -> str:
    """The text handed to the TTS engine for one episode.

    Args:
        episode: The episode to speak
        max_length: Truncate the body, handy for a fast end-to-end test
    """
    byline = f"Written by {episode.sender_name}" if episode.sender_name else ""
    text = episode.text[:max_length] if max_length else episode.text
    return f"{episode.title}\n{byline}\n\n{text}"


@serialized
def submit_tts_request_for_episodes(episodes: list[Episode]) -> None:
    """Submit the TTS request for the episodes."""
    for episode in episodes:
        try:
            tts_job_id = tts_service.submit_tts(episode_to_tts_text(episode))
        except Exception as e:
            print(f"TTS submit failed for episode {episode.id}: {e}")
            continue
        episode_ops.mark_tts_submitted(episode.id, tts_job_id)


def download_completed_tts_jobs(completed_jobs: list[str]) -> None:
    """Download the mp3 for the completed TTS jobs and update the database."""
    for job_id in completed_jobs:
        try:
            mp3_path = tts_service.download_mp3(job_id)
        except Exception as e:
            print(f"TTS download failed for job {job_id}: {e}")
            continue
        tts_service.delete_job(job_id)
        episode_ops.mark_tts_completed(job_id, os.path.basename(mp3_path))


def filter_job_ids_to_ones_we_know_about(job_ids: list[str]) -> list[str]:
    """Drop (and delete upstream) any TTS job we have no episode for."""
    known_job_ids = episode_ops.get_job_ids()
    result = []

    for job_id in job_ids:
        if job_id in known_job_ids:
            result.append(job_id)
        else:
            print("Removing unknown tts job with id", job_id)
            tts_service.delete_job(job_id)
    return result


@serialized
def tts_pending_and_completed_update() -> None:
    """Reconcile the TTS service and the database."""
    completed_jobs, ongoing_jobs = tts_service.get_jobs()

    completed_jobs = filter_job_ids_to_ones_we_know_about(completed_jobs)

    download_completed_tts_jobs(completed_jobs)
    known_jobs = set(completed_jobs) | set(ongoing_jobs)
    nulled_tts_jobs = episode_ops.null_episodes_that_tts_doesnt_know_about(known_jobs)
    for episode_id, tts_job_id in nulled_tts_jobs:
        print(f"Episode {episode_id} had job id {tts_job_id} but the TTS service doesn't know about it; will retry.")


@serialized
def reconcile_tts_state_on_boot() -> None:
    """Reconcile local TTS job state against the TTS service at startup.

    Only runs when there are in-flight jobs to check on, so we don't wake the
    TTS host on every container restart for nothing.
    """
    if not episode_ops.has_submitted_tts_jobs():
        print("Boot reconcile: no in-flight TTS jobs, skipping")
        return
    if not tts_service.wake_and_wait_healthy():
        print("Boot reconcile: TTS service not reachable, skipping")
        return
    print("Boot reconcile: checking for stale TTS job ids")
    try:
        tts_pending_and_completed_update()
    except Exception as e:
        print(f"Boot reconcile failed: {e}")


def retention_cutoff(now: datetime | None = None) -> datetime | None:
    """The UTC datetime before which episodes are aged off, None when retention is off."""
    if not Config.EPISODE_RETENTION_DAYS:
        return None
    return (now or datetime.now(UTC)) - timedelta(days=Config.EPISODE_RETENTION_DAYS)


@serialized
def age_off_episodes(cutoff: datetime) -> None:
    """Delete episodes published before the cutoff along with their audio and raw mail."""
    removed = episode_ops.delete_episodes_published_before(cutoff.replace(tzinfo=None))
    for mp3, raw_path in removed:
        delete_mp3(mp3)
        if raw_path and os.path.exists(raw_path):
            os.remove(raw_path)
    if removed:
        print(f"Aged off {len(removed)} episodes published before {cutoff}")


def ingest_new_emails() -> int:
    """Poll the configured IMAP mailbox. Returns the number of new episodes."""
    if not Config.imap_enabled():
        return 0

    source = ImapSource()
    created = []

    def persist(email):
        episode = process_incoming(email)
        if episode is not None:
            created.append(episode)

    try:
        source.fetch(on_message=persist)
    except Exception as e:
        print(f"IMAP poll failed: {e}")
        return 0

    if created:
        print(f"Ingested {len(created)} new episode(s) from IMAP")
    return len(created)


@serialized
def main_poll_loop() -> list[Episode]:
    """Age off, ingest, then move TTS work forward. The scheduled entrypoint."""
    cutoff = retention_cutoff()
    if cutoff:
        age_off_episodes(cutoff)

    ingest_new_emails()

    episodes_to_tts = episode_ops.get_episodes_to_tts()[: Config.TTS_BATCH_SIZE]
    has_new_work = len(episodes_to_tts) > 0
    has_inflight = episode_ops.has_submitted_tts_jobs()

    if not has_new_work and not has_inflight:
        print("No pending or in-flight TTS work, skipping TTS health check")
        return []

    if has_new_work:
        healthy = tts_service.wake_and_wait_healthy()
    else:
        healthy = tts_service.check_health()

    if not healthy:
        print("TTS service is not healthy, skipping TTS request")
        return []

    try:
        tts_pending_and_completed_update()
    except Exception as e:
        print(f"TTS reconcile failed: {e}")
        return []

    if has_new_work:
        submit_tts_request_for_episodes(episodes_to_tts)

    return episodes_to_tts
