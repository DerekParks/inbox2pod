"""Tests for the Flask app: pages, admin API, feed and token gating."""

import os
from datetime import UTC, datetime

import pytest
from conftest import fixture_bytes

from inbox2pod.config import Config
from inbox2pod.episodes import Episode, EpisodeOps

TOKEN = "x7kq2m4t7z"


@pytest.fixture
def ops():
    return EpisodeOps()


@pytest.fixture
def raw_email(tmp_storage):
    raw_dir, _ = tmp_storage
    return raw_dir


def build_episode(episode_id="ep-1", **kwargs):
    fields = {
        "id": episode_id,
        "feed": "default",
        "title": "The bond market is saying things",
        "description": "Bonds. Doing things.",
        "text": "Yields moved in a direction. " * 20,
        "sender_name": "Money Stuff",
        "sender_email": "news@dailyledger.example",
        "recipient": "pod@example.com",
        "published_at": datetime(2026, 2, 10, 19, 0),
        "received_at": datetime(2026, 2, 10, 20, 0),
        "raw_path": "/tmp/ep-1.eml",
        "extractor": "trafilatura",
        "extraction_ok": True,
    }
    fields.update(kwargs)
    return Episode(**fields)


@pytest.fixture
def stored(app_client, ops, tmp_storage):
    """An episode that has made it all the way to audio."""
    raw_dir, audio_dir = tmp_storage
    raw = raw_dir / "ep-1.eml"
    raw.write_bytes(fixture_bytes("money_stuff_longform.eml"))
    (audio_dir / "job-1.mp3").write_bytes(b"fake audio")

    episode = build_episode(raw_path=str(raw))
    ops.add_episode(episode)
    ops.mark_tts_submitted("ep-1", "job-1")
    ops.mark_tts_completed("job-1", "job-1.mp3")
    return ops.get_episode("ep-1")


class TestTokenGating:
    def test_everything_is_open_without_a_token(self, app_client, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", "")
        assert app_client.get("/").status_code == 200
        assert app_client.get("/feed.xml").status_code == 200

    def test_pages_are_locked_with_a_token(self, app_client, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        assert app_client.get("/").status_code == 401
        assert app_client.get("/feed.xml").status_code == 401
        assert app_client.get("/episodes/").status_code == 401

    def test_query_string_token_works(self, app_client, monkeypatch, stored):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        assert app_client.get(f"/feed.xml?token={TOKEN}").status_code == 200
        assert app_client.get(f"/?token={TOKEN}").status_code == 200

    def test_header_tokens_work(self, app_client, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        assert app_client.get("/feed.xml", headers={"X-Feed-Token": TOKEN}).status_code == 200
        assert app_client.get("/feed.xml", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200

    def test_a_wrong_token_is_rejected(self, app_client, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        assert app_client.get("/feed.xml?token=nope").status_code == 401
        assert app_client.get("/feed.xml", headers={"X-Feed-Token": "nope"}).status_code == 401

    def test_token_as_a_path_prefix(self, app_client, monkeypatch, stored):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        assert app_client.get(f"/{TOKEN}/feed.xml").status_code == 200
        assert app_client.get(f"/{TOKEN}/audio/job-1.mp3").status_code == 200
        assert app_client.get(f"/{TOKEN}/rss").status_code == 200
        assert app_client.get("/wrongtoken/audio/job-1.mp3").status_code == 401

    def test_open_paths_stay_open(self, app_client, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        monkeypatch.setattr("inbox2pod.pipeline.tts_service.check_health", lambda: True)
        assert app_client.get("/health").status_code == 200
        assert app_client.get("/cover.png").status_code == 200
        assert app_client.get("/feed.svg").status_code == 200

    def test_a_padded_token_is_trimmed(self, app_client, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        assert app_client.get(f"/feed.xml?token=%20{TOKEN}%20").status_code == 200


class TestEpisodesPage:
    def test_empty_state(self, app_client):
        page = app_client.get("/")
        assert page.status_code == 200
        assert b"No episodes yet" in page.data

    def test_lists_episodes_with_status(self, app_client, stored):
        page = app_client.get("/")
        assert b"The bond market is saying things" in page.data
        assert b"Money Stuff" in page.data
        assert b"ready" in page.data
        assert b"job-1 via trafilatura" in page.data

    def test_pending_and_failed_extraction_are_visible(self, app_client, ops):
        ops.add_episode(build_episode("ep-pending", extraction_ok=True))
        ops.add_episode(build_episode("ep-thin", extraction_ok=False))

        page = app_client.get("/")
        assert b"queued for TTS" in page.data
        assert b"extraction failed" in page.data

    def test_pagination(self, app_client, ops):
        for i in range(3):
            ops.add_episode(build_episode(f"ep-{i}", published_at=datetime(2026, 2, 10 + i, 19, 0)))

        first = app_client.get("/?page=1&per_page=2")
        assert first.status_code == 200
        assert first.data.count(b'<tr class="hover:bg-gray-50">') == 2
        assert b"/episode/ep-2" in first.data
        assert b"/episode/ep-1" in first.data
        assert b"/episode/ep-0" not in first.data

        last = app_client.get("/?page=2&per_page=2")
        assert last.data.count(b'<tr class="hover:bg-gray-50">') == 1
        assert b"/episode/ep-0" in last.data

    def test_page_numbers_out_of_range_clamp(self, app_client, ops):
        ops.add_episode(build_episode())
        page = app_client.get("/?page=99")
        assert page.status_code == 200
        assert b"/episode/ep-1" in page.data

    def test_ingest_source_status_is_shown(self, app_client, monkeypatch):
        monkeypatch.setattr(Config, "IMAP_HOST", "imap.test.example")
        monkeypatch.setattr(Config, "SMTP_ENABLED", False)
        page = app_client.get("/")
        assert b"IMAP" in page.data

    def test_404_for_an_unknown_episode(self, app_client):
        assert app_client.get("/episode/nope").status_code == 404


class TestEpisodeDetailPage:
    def test_shows_the_extracted_text(self, app_client, stored):
        page = app_client.get("/episode/ep-1")
        assert page.status_code == 200
        assert b"The bond market is saying things" in page.data
        assert b"trafilatura" in page.data

    def test_reports_whether_the_raw_email_is_stored(self, app_client, ops):
        ops.add_episode(build_episode("ep-noraw", raw_path="/tmp/definitely-not-here.eml"))
        page = app_client.get("/episode/ep-noraw")
        assert b"missing" in page.data.lower()


class TestEpisodeApi:
    def test_list(self, app_client, stored):
        response = app_client.get("/episodes/")
        assert response.status_code == 200
        body = response.get_json()
        assert [episode["id"] for episode in body] == ["ep-1"]
        assert body[0]["sender_email"] == "news@dailyledger.example"
        assert body[0]["mp3"] == "job-1.mp3"

    def test_tts_waiting(self, app_client, ops):
        ops.add_episode(build_episode("ep-waiting"))
        ops.add_episode(build_episode("ep-thin", extraction_ok=False))

        body = app_client.get("/episodes/tts_waiting").get_json()
        assert [episode["id"] for episode in body] == ["ep-waiting"]

    def test_force_update_runs_a_poll(self, app_client, monkeypatch):
        calls = []
        monkeypatch.setattr("inbox2pod.api.main_poll_loop", lambda: calls.append(1))

        assert app_client.post("/episodes/force_update").status_code == 200
        assert calls == [1]

    def test_delete_removes_the_row_audio_and_raw(self, app_client, ops, stored):
        raw_path = stored.raw_path
        audio = os.path.join(Config.MP3_STORAGE_PATH, "job-1.mp3")
        assert os.path.exists(audio)

        assert app_client.delete("/episodes/ep-1").status_code == 200

        assert ops.get_episode("ep-1") is None
        assert not os.path.exists(raw_path)
        assert not os.path.exists(audio)

    def test_delete_an_unknown_episode(self, app_client):
        assert app_client.delete("/episodes/nope").status_code == 404

    def test_re_synthesize_clears_tts_and_polls(self, app_client, ops, stored, monkeypatch):
        calls = []
        monkeypatch.setattr("inbox2pod.api.main_poll_loop", lambda: calls.append(1))

        assert app_client.delete("/episodes/tts/ep-1").status_code == 200

        assert ops.get_episode("ep-1").tts_job_id is None
        assert ops.get_episode("ep-1").mp3 is None
        assert not os.path.exists(os.path.join(Config.MP3_STORAGE_PATH, "job-1.mp3"))
        assert calls == [1]

    def test_re_synthesize_an_unknown_episode(self, app_client):
        assert app_client.delete("/episodes/tts/nope").status_code == 404

    def test_re_extract_reports_what_it_got(self, app_client, ops, stored):
        ops.update_extraction("ep-1", text="stale", description="stale", extractor="trafilatura", extraction_ok=True)

        response = app_client.post("/episodes/re_extract/ep-1")
        assert response.status_code == 200
        body = response.get_json()
        assert body["extractor"] == "trafilatura"
        assert body["extraction_ok"] is True
        assert body["chars"] > 200

    def test_re_extract_without_a_raw_file(self, app_client, ops):
        ops.add_episode(build_episode("ep-noraw", raw_path="/tmp/definitely-not-here.eml"))
        assert app_client.post("/episodes/re_extract/ep-noraw").status_code == 409

    def test_re_extract_an_unknown_episode(self, app_client):
        assert app_client.post("/episodes/re_extract/nope").status_code == 404


class TestFeed:
    def test_feed_lists_only_synthesized_episodes(self, app_client, ops):
        ops.add_episode(build_episode("ep-no-audio"))
        feed = app_client.get("/feed.xml").data.decode()

        assert "ep-no-audio" not in feed
        assert "<rss" in feed

    def test_feed_contains_the_episode_and_audio_url(self, app_client, stored):
        feed = app_client.get("/feed.xml").data.decode()

        assert "The bond market is saying things" in feed
        assert "audio/job-1.mp3" in feed
        assert "news@dailyledger.example" in feed
        assert "<itunes:author>Money Stuff</itunes:author>" in feed

    def test_feed_carries_the_token_into_audio_urls(self, app_client, monkeypatch, stored):
        monkeypatch.setattr(Config, "FEED_TOKEN", TOKEN)
        feed = app_client.get(f"/feed.xml?token={TOKEN}").data.decode()

        assert f"http://localhost/{TOKEN}/audio/job-1.mp3" in feed
        assert f"http://localhost/{TOKEN}/feed.xml" in feed

    def test_feed_metadata(self, app_client, stored):
        feed = app_client.get("/feed.xml").data.decode()
        assert "Test Inbox" in feed
        assert "cover.png" in feed
        assert "<language>en-us</language>" in feed

    def test_feed_is_trimmed_to_the_newest_episodes(self, app_client, ops, monkeypatch, tmp_storage):
        monkeypatch.setattr(Config, "FEED_MAX_EPISODES", 2)
        for i in range(4):
            episode = build_episode(f"ep-{i}", published_at=datetime(2026, 2, 10 + i, 19, 0, tzinfo=UTC))
            ops.add_episode(episode)
            ops.mark_tts_submitted(episode.id, f"job-{i}")
            ops.mark_tts_completed(f"job-{i}", f"job-{i}.mp3")

        feed = app_client.get("/feed.xml").data.decode()
        assert "job-2.mp3" in feed
        assert "job-3.mp3" in feed
        assert "job-0.mp3" not in feed

    def test_api_feed_route(self, app_client, stored):
        response = app_client.get("/episodes/feed")
        assert response.status_code == 200
        assert b"<rss" in response.data

    def test_atom_and_rss_aliases(self, app_client, stored):
        for path in ("/feed", "/feed.rss", "/rss", "/atom"):
            assert app_client.get(path).status_code == 200


class TestHealth:
    def test_health_reports_the_wiring(self, app_client, ops, monkeypatch):
        monkeypatch.setattr("inbox2pod.pipeline.tts_service.check_health", lambda: False)
        body = app_client.get("/health").get_json()

        assert body["status"] == "healthy"
        assert body["episodes"] == 0
        assert body["imap_enabled"] is True
        assert body["smtp_enabled"] is False
        assert body["smtp_running"] is False
        assert body["tts_healthy"] is False


def test_feed_uses_configured_external_origin(app_client, monkeypatch, stored):
    monkeypatch.setattr(Config, "FEED_BASE_URL", "https://podbox.example.ts.net")
    monkeypatch.setattr(Config, "FEED_TOKEN", "secret")
    response = app_client.get("/secret/feed.xml")
    assert response.status_code == 200
    assert b"https://podbox.example.ts.net/secret/feed.xml" in response.data
    assert b"https://podbox.example.ts.net/cover.png" in response.data
    assert b"https://podbox.example.ts.net/secret/audio/job-1.mp3" in response.data
    assert b"http://localhost" not in response.data
