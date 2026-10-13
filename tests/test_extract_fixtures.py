"""Corpus-level extraction tests: every fixture must come out listenable."""

import pytest
from conftest import FIXTURES, fixture_bytes, fixture_names

from inbox2pod.config import Config
from inbox2pod.extract import extract_email

NEVER_IN_OUTPUT = [
    "unsubscribe",
    "manage preferences",
    "view this email in your browser",
    "all rights reserved",
    "you are receiving this email",
    "REDACTED",
    "track.example",
    "http",
    "&#",
    "&amp;",
    "\ufffd",
    "Ã¢",
]


def _extract(name: str):
    return extract_email(fixture_bytes(name))


@pytest.mark.parametrize("name", fixture_names())
def test_extraction_never_raises(name):
    result = _extract(name)
    assert result.text is not None
    assert result.extractor


@pytest.mark.parametrize("name", fixture_names())
def test_no_newsletter_chrome_survives(name):
    text = _extract(name).text.lower()
    for needle in [needle.lower() for needle in NEVER_IN_OUTPUT]:
        assert needle not in text, f"{name} kept {needle!r}"


@pytest.mark.parametrize("name", fixture_names())
def test_description_is_short(name):
    description = _extract(name).description
    assert len(description) <= Config.DESCRIPTION_LENGTH + 10


def test_long_form_article():
    result = _extract("money_stuff_longform.eml")
    assert result.ok
    assert result.extractor == "trafilatura"
    assert "eleven points" in result.text
    assert "Amy Field" in result.text
    assert "20% off your first year" not in result.text
    assert "SPONSORED" not in result.text


def test_digest_keeps_every_item():
    result = _extract("tldr_digest.eml")
    assert result.ok
    for headline in [
        "Spotify raises prices again",
        "The chip export carve-out nobody asked for",
        "Why your airline ticket got more complicated",
        "A boring win for transit",
        "One more thing about the housing data",
    ]:
        assert headline in result.text
    assert "Use code LEDGER" not in result.text
    assert "Forward to a friend" not in result.text


def test_image_heavy_email_still_produces_text():
    result = _extract("image_heavy_product.eml")
    assert result.ok
    assert "Spring 2026" in result.text
    assert "Free shipping on orders over $75" in result.text
    assert "SHOP NEW ARRIVALS" not in result.text


def test_plain_text_only_newsletter():
    result = _extract("plain_only.eml")
    assert result.extractor == "plain"
    assert result.ok
    assert "a physical button" in result.text
    assert "19 hours measured" in result.text
    assert "I spent ten days with the new version of the field recorder" in result.text
    assert "Sent with Ghost" not in result.text


def test_mojibake_is_repaired():
    result = _extract("mojibake_bad_charset.eml")
    assert result.ok
    assert "The bank's \"prudent\" stance" in result.text
    assert "\u00e2\u0080\u0099" not in result.text
    assert "\u00a340 million" in result.text


def test_missing_message_id_still_extracts():
    result = _extract("no_message_id.eml")
    assert result.ok
    assert "Markets spent most of Tuesday" in result.text


def test_tracking_pixel_only_email_fails_extraction():
    result = _extract("tracking_pixel_only.eml")
    assert result.text == ""
    assert result.ok is False
    assert result.description == ""


def test_quoted_reply_is_dropped():
    result = _extract("reply_thread.eml")
    assert result.ok
    assert "warehouse deal closes Thursday" in result.text
    assert "insurance rider" not in result.text
    assert "wrote:" not in result.text


def test_attachment_is_ignored_body_is_kept():
    result = _extract("attachment_and_body.eml")
    assert result.ok
    assert "Markets spent most of Tuesday" in result.text
    assert "appendix.pdf" not in result.text


def test_broken_headers_still_extract():
    result = _extract("broken_headers.eml")
    assert result.ok
    assert "Equity breadth" in result.text


def test_forwarded_message_keeps_the_forwarded_content():
    result = _extract("forwarded_nested.eml")
    assert result.ok
    assert "Forwarding this along" in result.text
    assert "Equity breadth" in result.text


def test_substack_style_post():
    result = _extract("substack_style.eml")
    assert result.ok
    assert "eleven points" in result.text
    assert "Join the discussion" not in result.text
    assert "to get it every Tuesday" not in result.text


def test_re_extraction_is_stable():
    name = "money_stuff_longform.eml"
    raw = fixture_bytes(name)
    assert extract_email(raw).text == extract_email(raw).text


def test_garbage_bytes_do_not_raise(tmp_path):
    junk = tmp_path / "junk.eml"
    junk.write_bytes(b"this is not an email at all\x00\x01\x02")
    result = extract_email(junk.read_bytes())
    assert result.ok is False


def test_fixture_directory_is_populated():
    assert len(fixture_names()) >= 10
    assert FIXTURES
