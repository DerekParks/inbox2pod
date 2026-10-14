"""Tests for the shared helpers."""

import os
from datetime import UTC, datetime, timedelta, timezone

import pytest

from inbox2pod.utils import (
    PROJECT_ROOT,
    clean_subject,
    resolve_path,
    sanitize_xml_string,
    sha256_hex,
    to_utc,
    utcnow_naive,
)


class TestResolvePath:
    def test_absolute_paths_pass_through(self):
        assert resolve_path("/var/lib/inbox2pod/audio") == "/var/lib/inbox2pod/audio"

    def test_relative_paths_hang_off_the_project_root(self):
        assert resolve_path("data/audio") == os.path.join(PROJECT_ROOT, "data", "audio")

    def test_dot_segments_are_collapsed(self):
        assert resolve_path("data/../data/audio") == os.path.join(PROJECT_ROOT, "data", "audio")


class TestHashing:
    def test_text_and_its_utf8_bytes_hash_the_same(self):
        assert sha256_hex("newsletter") == sha256_hex(b"newsletter")

    def test_the_digest_is_stable_and_hex(self):
        digest = sha256_hex("abc@example.com")
        assert digest == sha256_hex("abc@example.com")
        assert len(digest) == 64
        assert all(char in "0123456789abcdef" for char in digest)

    def test_different_input_different_digest(self):
        assert sha256_hex("a") != sha256_hex("b")


class TestDatetimes:
    def test_naive_datetimes_are_declared_utc(self):
        naive = datetime(2026, 2, 10, 19, 0)
        assert to_utc(naive) == datetime(2026, 2, 10, 19, 0, tzinfo=UTC)

    def test_aware_datetimes_are_converted_not_relabelled(self):
        aware = datetime(2026, 2, 10, 14, 0, tzinfo=timezone(timedelta(hours=-5)))
        assert to_utc(aware) == datetime(2026, 2, 10, 19, 0, tzinfo=UTC)

    def test_utcnow_naive_is_naive_and_recent(self):
        now = utcnow_naive()
        assert now.tzinfo is None
        assert abs(datetime.now(UTC).replace(tzinfo=None) - now) < timedelta(seconds=5)


class TestCleanSubject:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Hello there", "Hello there"),
            ("Re: Hello there", "Hello there"),
            ("RE: RE: Hello there", "Hello there"),
            ("Fwd: Hello there", "Hello there"),
            ("FW: Hello there", "Hello there"),
            ("Aw: Hello there", "Hello there"),
            ("SV: Hello there", "Hello there"),
            ("答复: 你好", "你好"),
            ("轉寄: 你好", "你好"),
            ("[Money Stuff] The bond market", "The bond market"),
            ("[morning brew] Coffee prices", "Coffee prices"),
            ("(News) Big news", "Big news"),
            ("Re: [Money Stuff] Bond yields", "Bond yields"),
            ("  Lots   of   space  ", "Lots of space"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_cleaning(self, raw, expected):
        assert clean_subject(raw) == expected

    def test_a_tag_in_the_middle_is_left_alone(self):
        assert clean_subject("Notes [draft] v2") == "Notes [draft] v2"

    def test_a_subject_that_is_only_a_tag_does_not_become_something_else(self):
        assert clean_subject("[RE]") == ""

    def test_a_colon_is_required_for_a_reply_prefix(self):
        assert clean_subject("Ready when you are") == "Ready when you are"

    def test_full_width_colons_are_accepted(self):
        assert clean_subject("Re：Hello") == "Hello"


class TestSanitizeXmlString:
    def test_plain_text_is_untouched(self):
        assert sanitize_xml_string("Rates rose 3% — again") == "Rates rose 3% — again"

    def test_none_becomes_empty(self):
        assert sanitize_xml_string(None) == ""

    def test_null_bytes_are_dropped(self):
        assert sanitize_xml_string("clean\x00text") == "cleantext"

    def test_other_control_characters_become_spaces(self):
        assert sanitize_xml_string("bad\x01\x02chars") == "bad  chars"

    def test_tab_newline_and_carriage_return_survive(self):
        assert sanitize_xml_string("a\tb\nc\rd") == "a\tb\nc\rd"

    def test_non_strings_are_stringified(self):
        assert sanitize_xml_string(42) == "42"

    def test_emojis_are_kept(self):
        assert sanitize_xml_string("markets 📈") == "markets 📈"

    def test_0xfffe_and_0xffff_are_dropped(self):
        # both are forbidden by XML 1.0 even though Python strings can hold them
        assert sanitize_xml_string("a\ufffeb\uffff") == "ab"
