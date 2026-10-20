"""
Database operations for episodes
"""

import os
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from inbox2pod.config import Config

# Database setup
Base = declarative_base()
_engines = {}


def _ensure_sqlite_dir(database_uri: str) -> None:
    """Create the parent directory of a relative SQLite database file."""
    prefix = "sqlite:///"
    if not database_uri.startswith(prefix):
        return
    path = database_uri[len(prefix) :]
    if not path or path == ":memory:":
        return
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)


def get_engine():
    """Get (and on first call create) the engine for the configured database."""
    engine = _engines.get(Config.DATABASE_URI)
    if engine is None:
        _ensure_sqlite_dir(Config.DATABASE_URI)
        engine = create_engine(Config.DATABASE_URI, future=True)
        Base.metadata.create_all(engine)
        _engines[Config.DATABASE_URI] = engine
    return engine


def make_session():
    """Get a session; returned objects stay readable after it closes."""
    return sessionmaker(bind=get_engine(), future=True, expire_on_commit=False)()


class Episode(Base):
    """One episode, made from one email."""

    __tablename__ = "episodes"

    id = Column(String, primary_key=True)
    feed = Column(String, default="default", nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    text = Column(Text)
    sender_name = Column(String)
    sender_email = Column(String)
    recipient = Column(String)
    published_at = Column(DateTime, nullable=False)
    received_at = Column(DateTime, nullable=False)
    raw_path = Column(String, nullable=False)
    extractor = Column(String)
    extraction_ok = Column(Boolean, default=True, nullable=False)
    tts_job_id = Column(String)
    mp3 = Column(String)



class EpisodeOps:
    """Operations for the Episode model."""

    def get_episode(self, episode_id: str) -> Episode | None:
        """Get one episode by id."""
        with make_session() as session:
            return session.get(Episode, episode_id)

    def episode_exists(self, episode_id: str) -> bool:
        """Whether an episode id is already stored."""
        with make_session() as session:
            return session.query(Episode.id).filter_by(id=episode_id).first() is not None

    def get_episodes_with_mp3(self, feed: str | None = None) -> list[Episode]:
        """Get the episodes with mp3, oldest first.

        Args:
            feed: Only episodes on this feed; None for every feed
        """
        with make_session() as session:
            query = session.query(Episode).filter(Episode.mp3 != None)  # noqa: E711
            if feed:
                query = query.filter(Episode.feed == feed)
            return query.order_by(Episode.published_at.asc()).all()

    def get_episode_ids(self) -> set[str]:
        """Get the episode ids."""
        with make_session() as session:
            return {episode.id for episode in session.query(Episode).all()}

    def get_episodes_to_tts(self) -> list[Episode]:
        """Get the episodes that haven't been processed by TTS yet.

        Episodes whose extraction came back empty are skipped: they would turn
        into four-second episodes of nothing.
        """
        with make_session() as session:
            return (
                session.query(Episode)
                .filter(
                    Episode.tts_job_id == None,  # noqa: E711
                    Episode.extraction_ok.is_(True),
                    Episode.text != None,  # noqa: E711
                )
                .order_by(Episode.published_at.asc())
                .all()
            )

    def has_submitted_tts_jobs(self) -> bool:
        """Return True if there are episodes with a TTS job id but no mp3 yet."""
        with make_session() as session:
            return (
                session.query(Episode)
                .filter(Episode.tts_job_id != None, Episode.mp3 == None)  # noqa: E711
                .first()
                is not None
            )

    def get_job_ids(self) -> set[str]:
        """Get the TTS job ids this database is waiting on."""
        with make_session() as session:
            return {episode.tts_job_id for episode in session.query(Episode).all() if episode.tts_job_id}

    def null_episodes_that_tts_doesnt_know_about(self, known_jobs: set[str]) -> list[tuple[str, str]]:
        """Null the tts_job_id of local episodes whose job the TTS service no longer tracks.

        Args:
            known_jobs: Job ids the TTS service currently reports (union of
                completed and ongoing; failed/errored jobs should be excluded
                so they get retried).

        Returns:
            list[tuple[str, str]]: (episode_id, old_job_id) pairs that were nulled.
        """
        result = []
        with make_session() as session:
            waiting_episodes = (
                session.query(Episode)
                .filter(Episode.tts_job_id != None, Episode.mp3 == None)  # noqa: E711
                .all()
            )
            for episode in waiting_episodes:
                if episode.tts_job_id not in known_jobs:
                    old_id = episode.tts_job_id
                    episode.tts_job_id = None
                    result.append((episode.id, old_id))
            session.commit()
        return result

    def mark_tts_completed(self, job_id: str, mp3_path: str) -> bool:
        """Record the mp3 produced by a finished TTS job.

        Returns:
            bool: False when no episode is waiting on that job id.
        """
        with make_session() as session:
            episode = session.query(Episode).filter_by(tts_job_id=job_id).first()
            if episode is None:
                return False
            episode.mp3 = mp3_path
            session.commit()
            return True

    def mark_tts_submitted(self, episode_id: str, job_id: str) -> bool:
        """Mark an episode as submitted to TTS."""
        with make_session() as session:
            episode = session.get(Episode, episode_id)
            if episode is None:
                return False
            episode.tts_job_id = job_id
            session.commit()
            return True

    def add_episode(self, episode: Episode) -> bool:
        """Add an episode, ignoring a duplicate id.

        Returns:
            bool: True when the episode was inserted, False when the id already existed.
        """
        with make_session() as session:
            if session.get(Episode, episode.id) is not None:
                return False
            session.add(episode)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if session.get(Episode, episode.id) is None:
                    raise
                return False
            return True

    def update_extraction(
        self,
        episode_id: str,
        text: str,
        description: str | None,
        extractor: str | None,
        extraction_ok: bool,
    ) -> bool:
        """Replace the extracted text of an episode (used by re-extract-from-raw)."""
        with make_session() as session:
            episode = session.get(Episode, episode_id)
            if episode is None:
                return False
            episode.text = text
            episode.description = description
            episode.extractor = extractor
            episode.extraction_ok = extraction_ok
            session.commit()
            return True

    def delete_episode(self, episode_id: str) -> int:
        """Delete an episode from the database."""
        with make_session() as session:
            result = session.query(Episode).filter(Episode.id == episode_id).delete()
            session.commit()
            return result

    def delete_episodes_published_before(self, cutoff: datetime) -> list[tuple[str | None, str | None]]:
        """Delete episodes published before the cutoff.

        Args:
            cutoff: Naive UTC datetime

        Returns:
            list[tuple[str | None, str | None]]: (mp3 filename, raw path) for each
                deleted episode; either half is None when the episode had neither.
        """
        with make_session() as session:
            expired = session.query(Episode).filter(Episode.published_at < cutoff)
            files = [(episode.mp3, episode.raw_path) for episode in expired]
            expired.delete()
            session.commit()
            return files

    def get_episodes_page(self, page: int, per_page: int) -> tuple[list[Episode], int]:
        """Get one page of episodes, newest first.

        Returns:
            tuple[list[Episode], int]: The episodes on the page and the total episode count
        """
        with make_session() as session:
            query = session.query(Episode)
            total = query.count()
            episodes = (
                query.order_by(Episode.published_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
            )
            return episodes, total

    def get_all_episodes(self, sort_by_published_at: bool = False) -> list[Episode]:
        """Get all episodes."""
        with make_session() as session:
            if sort_by_published_at:
                return session.query(Episode).order_by(Episode.published_at.desc()).all()
            return session.query(Episode).all()

    def get_latest_episode_date(self) -> datetime | None:
        """The newest published_at in the database."""
        with make_session() as session:
            episode = session.query(Episode).order_by(Episode.published_at.desc()).first()
            return episode.published_at if episode else None

    def clear_tts(self, episode_id: str) -> bool:
        """Clear the TTS job id and mp3 path for an episode."""
        with make_session() as session:
            episode = session.get(Episode, episode_id)
            if episode is None:
                return False
            episode.tts_job_id = None
            episode.mp3 = None
            session.commit()
            return True

    def get_episode_mp3(self, episode_id: str) -> str | None:
        """Get an episode's mp3 path by id."""
        with make_session() as session:
            episode = session.get(Episode, episode_id)
            return episode.mp3 if episode else None
