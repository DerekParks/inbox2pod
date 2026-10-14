"""Tests for RSS/podcast feed generation."""

from datetime import datetime
from xml.etree import ElementTree

import pytest

from inbox2pod.config import Config
from inbox2pod.episodes import Episode, EpisodeOps
from inbox2pod.feed import audio_url, enclosure_length, feed_url, gen_feed, owner_email, token_prefix

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM = "http://www.w3.org/2005/Atom"
ROOT_URL = "http://pod.example.com/"


def make_episode(episode_id="ep-1", **kwargs):
    fields = {
        "id": episode_id,
        "feed": "default",
        "title": "The bond market is saying things",
        "description": "Bonds are doing a thing.",
        "text": "Yields moved.",
        "sender_name": "Money Stuff",
        "sender_email": "news@dailyledger.example",
        "recipient": "pod@example.com",
        "published_at": datetime(2026, 2, 10, 19, 0),
        "received_at": datetime(2026, 2, 10, 20, 0),
        "raw_path": "/tmp/ep-1.eml",
        "extractor": "trafilatura",
        "extraction_ok": True,
        "mp3": "job-1.mp3",
    }
    fields.update(kwargs)
    return Episode(**fields)


@pytest.fixture
def audio(tmp_storage):
    _, audio_dir = tmp_storage
    (audio_dir / "job-1.mp3").write_bytes(b"x" * 1234)
    return audio_dir


def parse(feed: bytes) -> ElementTree.Element:
    return ElementTree.fromstring(feed)


class TestUrls:
    def test_audio_url_without_a_token(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", "")
        assert audio_url(ROOT_URL, "job-1.mp3") == "http://pod.example.com/audio/job-1.mp3"

    def test_audio_url_carries_the_token_as_a_path_prefix(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", "x7kq2m")
        assert audio_url(ROOT_URL, "job-1.mp3") == "http://pod.example.com/x7kq2m/audio/job-1.mp3"

    def test_a_path_in_the_mp3_column_is_reduced_to_a_filename(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", "")
        assert audio_url(ROOT_URL, "/var/lib/audio/job-1.mp3") == "http://pod.example.com/audio/job-1.mp3"

    def test_feed_url_follows_the_token(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", "x7kq2m")
        assert feed_url(ROOT_URL) == "http://pod.example.com/x7kq2m/feed.xml"

    def test_token_prefix_is_empty_without_a_token(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TOKEN", "")
        assert token_prefix(ROOT_URL) == ""


class TestEnclosureLength:
    def test_real_file_size(self, audio):
        assert enclosure_length("job-1.mp3") == 1234

    def test_missing_file_is_zero(self, audio):
        assert enclosure_length("nope.mp3") == 0

    def test_absolute_paths_work(self, audio):
        assert enclosure_length(str(audio / "job-1.mp3")) == 1234


class TestOwnerEmail:
    def test_configured_email_wins(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_OWNER_EMAIL", "me@pod.example")
        monkeypatch.setattr(Config, "IMAP_USER", "pod@mail.example")
        assert owner_email() == "me@pod.example"

    def test_falls_back_to_the_mailbox_user(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_OWNER_EMAIL", "")
        monkeypatch.setattr(Config, "IMAP_USER", "pod@mail.example")
        assert owner_email() == "pod@mail.example"

    def test_never_returns_empty(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_OWNER_EMAIL", "")
        monkeypatch.setattr(Config, "IMAP_USER", None)
        assert "@" in owner_email()


class TestFeed:
    def test_channel_metadata(self, monkeypatch, audio):
        monkeypatch.setattr(Config, "FEED_TITLE", "Inbox Letters")
        monkeypatch.setattr(Config, "FEED_DESCRIPTION", "Read to me")
        monkeypatch.setattr(Config, "FEED_AUTHOR", "Derek")
        monkeypatch.setattr(Config, "FEED_OWNER_EMAIL", "derek@example.com")
        monkeypatch.setattr(Config, "FEED_LANGUAGE", "en-gb")

        channel = parse(gen_feed([make_episode()], ROOT_URL)).find("channel")

        assert channel.findtext("title") == "Inbox Letters"
        assert channel.findtext("description") == "Read to me"
        assert channel.findtext("language") == "en-gb"
        assert channel.findtext(f"{{{ITUNES}}}author") == "Derek"
        assert channel.findtext(f"{{{ITUNES}}}explicit") == "no"
        assert channel.findtext(f"{{{ITUNES}}}owner/{{{ITUNES}}}email") == "derek@example.com"
        assert channel.find(f"{{{ITUNES}}}image").get("href") == "http://pod.example.com/cover.png"

    def test_self_link_carries_the_token(self, monkeypatch, audio):
        monkeypatch.setattr(Config, "FEED_TOKEN", "x7kq2m")
        channel = parse(gen_feed([make_episode()], ROOT_URL)).find("channel")

        assert channel.find(f"{{{ATOM}}}link").get("rel") == "self"
        assert channel.find(f"{{{ATOM}}}link").get("href") == "http://pod.example.com/x7kq2m/feed.xml"

    def test_site_url_override(self, monkeypatch, audio):
        monkeypatch.setattr(Config, "FEED_SITE_URL", "https://podcast.example.com/")
        channel = parse(gen_feed([make_episode()], ROOT_URL)).find("channel")

        assert channel.findtext("link") == "https://podcast.example.com/"

    def test_item_fields(self, audio):
        item = parse(gen_feed([make_episode()], ROOT_URL)).find("channel/item")

        assert item.findtext("title") == "The bond market is saying things"
        assert item.findtext("description") == "Bonds are doing a thing."
        assert item.findtext("guid") == "ep-1"
        assert item.find("guid").get("isPermaLink") == "false"
        assert item.findtext("pubDate") == "Tue, 10 Feb 2026 19:00:00 +0000"
        assert item.findtext("author").startswith("news@dailyledger.example")
        assert item.findtext(f"{{{ITUNES}}}author") == "Money Stuff"

        enclosure = item.find("enclosure")
        assert enclosure.get("url") == "http://pod.example.com/audio/job-1.mp3"
        assert enclosure.get("length") == "1234"
        assert enclosure.get("type") == "audio/mpeg"

    def test_an_episode_without_a_sender_name_still_has_an_author(self, audio):
        item = parse(gen_feed([make_episode(sender_name="")], ROOT_URL)).find("channel/item")
        assert item.findtext(f"{{{ITUNES}}}author") == "news@dailyledger.example"

    def test_the_newest_episode_comes_first(self, audio):
        episodes = [
            make_episode("ep-old", published_at=datetime(2026, 2, 10, 19, 0)),
            make_episode("ep-new", published_at=datetime(2026, 2, 11, 19, 0)),
        ]
        ids = [item.findtext("guid") for item in parse(gen_feed(episodes, ROOT_URL)).findall("channel/item")]
        assert ids == ["ep-new", "ep-old"]

    def test_an_empty_feed_is_still_valid(self, monkeypatch):
        monkeypatch.setattr(Config, "FEED_TITLE", "Nothing yet")
        channel = parse(gen_feed([], ROOT_URL)).find("channel")
        assert channel.findtext("title") == "Nothing yet"
        assert channel.findall("item") == []

    def test_control_characters_in_a_subject_do_not_break_the_xml(self, audio):
        episode = make_episode(title="Rates\x00 rise\x07today")
        feed = gen_feed([episode], ROOT_URL)

        assert "Rates rise today" in parse(feed).find("channel/item/title").text

    def test_unicode_survives(self, audio):
        episode = make_episode(title="Markt 📈 — «Zinsen»", sender_name="Nius 📬")
        root = parse(gen_feed([episode], ROOT_URL))

        assert root.find("channel/item/title").text == "Markt 📈 — «Zinsen»"
        assert root.find(f"channel/item/{{{ITUNES}}}author").text == "Nius 📬"

    def test_feed_is_bytes_with_an_xml_declaration(self, audio):
        feed = gen_feed([make_episode()], ROOT_URL)
        assert isinstance(feed, bytes)
        assert feed.startswith(b"<?xml")


class TestFeedAgainstTheDatabase:
    def test_the_whole_path_from_rows_to_rss(self, tmp_storage, audio):
        ops = EpisodeOps()
        ops.add_episode(make_episode("ep-1"))
        ops.add_episode(make_episode("ep-2", mp3="job-2.mp3", published_at=datetime(2026, 2, 11, 19, 0)))

        episodes = ops.get_episodes_with_mp3()
        ids = [item.findtext("guid") for item in parse(gen_feed(episodes, ROOT_URL)).findall("channel/item")]

        assert ids == ["ep-2", "ep-1"]

    def test_dangling_audio_does_not_explode_the_feed(self, audio):
        ops = EpisodeOps()
        ops.add_episode(make_episode("ep-1", mp3="never-downloaded.mp3"))

        item = parse(gen_feed(ops.get_episodes_with_mp3(), ROOT_URL)).find("channel/item")
        assert item.find("enclosure").get("length") == "0"
