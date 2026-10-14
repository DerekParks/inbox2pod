"""
RSS feed generation with the podcast extension.
"""

import os

from feedgen.feed import FeedGenerator

from inbox2pod.config import Config
from inbox2pod.episodes import Episode
from inbox2pod.utils import sanitize_xml_string, to_utc, utcnow_naive


def token_prefix(root_url: str) -> str:
    """The path-prefix form of the feed token: some podcast apps strip query params."""
    return f"{Config.FEED_TOKEN}/" if Config.FEED_TOKEN else ""


def audio_url(root_url: str, mp3: str) -> str:
    """Public URL for a stored mp3, carrying the feed token when one is set."""
    return f"{root_url}{token_prefix(root_url)}audio/{os.path.basename(mp3)}"


def feed_url(root_url: str) -> str:
    """Public URL of the feed itself."""
    return f"{root_url}{token_prefix(root_url)}feed.xml"


def enclosure_length(mp3: str) -> int:
    """Size of the audio file in bytes; podcast apps use it, 0 when unavailable."""
    path = mp3 if os.path.isabs(mp3) else os.path.join(Config.MP3_STORAGE_PATH, mp3)
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def owner_email() -> str:
    """Contact address for the feed owner, required by Apple Podcasts."""
    return Config.FEED_OWNER_EMAIL or Config.IMAP_USER or "nobody@example.com"


def gen_feed(episodes: list[Episode], root_url: str) -> bytes:
    """Generate an RSS feed of the episodes.

    Args:
        episodes: The episodes to include, oldest first
        root_url: The root URL of this inbox2pod instance, with a trailing slash
    """
    site_url = Config.FEED_SITE_URL or root_url
    title = Config.FEED_TITLE
    description = Config.FEED_DESCRIPTION

    fg = FeedGenerator()
    fg.id(sanitize_xml_string(feed_url(root_url)))
    fg.title(sanitize_xml_string(title))
    # feedgen uses the *last* link for the RSS <link> element, so the self link
    # has to be registered before the site link
    fg.link(href=sanitize_xml_string(feed_url(root_url)), rel="self")
    fg.link(href=sanitize_xml_string(site_url), rel="alternate")
    fg.language(Config.FEED_LANGUAGE)
    fg.description(sanitize_xml_string(description))
    fg.load_extension("podcast")
    fg.podcast.itunes_author(sanitize_xml_string(Config.FEED_AUTHOR))
    fg.podcast.itunes_image(f"{root_url}cover.png")
    fg.podcast.itunes_owner(name=sanitize_xml_string(Config.FEED_AUTHOR), email=owner_email())
    fg.podcast.itunes_explicit("no")
    fg.lastBuildDate(to_utc(utcnow_naive()))

    for episode in episodes:
        enclosure = audio_url(root_url, episode.mp3)
        # prepend, so handing us the episodes oldest-first puts the newest on top
        entry = fg.add_entry(order="prepend")
        entry.id(sanitize_xml_string(episode.id))
        entry.title(sanitize_xml_string(episode.title))
        entry.link({"href": sanitize_xml_string(enclosure), "rel": "alternate"})
        entry.description(sanitize_xml_string(episode.description or ""))
        entry.published(to_utc(episode.published_at))
        entry.updated(to_utc(episode.received_at))
        author_name = episode.sender_name or episode.sender_email
        if author_name:
            entry.author(
                {
                    "name": sanitize_xml_string(author_name),
                    "email": sanitize_xml_string(episode.sender_email or owner_email()),
                }
            )
            entry.podcast.itunes_author(sanitize_xml_string(author_name))
        entry.enclosure(enclosure, enclosure_length(episode.mp3), "audio/mpeg")
        entry.podcast.itunes_image(f"{root_url}cover.png")

    return fg.rss_str(pretty=True)
