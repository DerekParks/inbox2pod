"""Unit tests for each speakability heuristic in extract.py."""

from inbox2pod.extract import (
    clean_email_html,
    collapse_whitespace,
    cut_footer,
    decode_part_bytes,
    drop_boilerplate_lines,
    html_to_text,
    is_boilerplate_line,
    make_description,
    make_speakable,
    plain_text_to_speakable,
    strip_emoji,
    strip_markdown,
    strip_urls,
    strip_zero_width,
    unflow_plain_text,
)


class TestFixMojibake:
    def test_repairs_utf8_read_as_latin1(self):
        assert make_speakable("The bank\u00e2\u0080\u0099s prudent stance") == "The bank's prudent stance"

    def test_normalizes_curly_quotes_and_dashes(self):
        assert make_speakable("\u201chello\u201d \u2014 fine\u2026") == '"hello" - fine...'

    def test_normalizes_nbsp(self):
        assert make_speakable("cost\u00a0$40\u00a0million") == "cost $40 million"


class TestZeroWidth:
    def test_strips_preheader_invisible_chars(self):
        assert strip_zero_width("junk\u200b\u200c\u200d\ufeff\u00ad text") == "junk text"

    def test_html_entity_invisible_chars_gone_after_full_pass(self):
        assert make_speakable("Real\u200b news \u2060here") == "Real news here"


class TestStripUrls:
    def test_removes_bare_url_keeps_surrounding_prose(self):
        assert strip_urls("As reported at https://example.com/story yesterday") == "As reported at yesterday"

    def test_markdown_link_keeps_text(self):
        assert strip_urls("Read [the market letter](https://example.com/ml/1) today") == "Read the market letter today"

    def test_removes_markdown_image_entirely(self):
        assert strip_urls("Look ![hero image](https://cdn.example.com/a.jpg) at this") == "Look at this"

    def test_removes_angle_bracket_url(self):
        assert strip_urls("write to <mailto:me@example.com> please") == "write to please"

    def test_removes_email_addresses(self):
        assert strip_urls("reply to hello@dailyledger.example to win") == "reply to to win"

    def test_drops_line_that_is_only_a_link(self):
        assert strip_urls("https://example.com/a/b/c") == ""

    def test_drops_line_that_is_mostly_a_link(self):
        assert strip_urls("Sign up at https://example.com/r/abcdef") == ""

    def test_strips_bare_domain_with_path(self):
        assert strip_urls("Full details at example.com/market-letter/2026-02-11 for members") == (
            "Full details at for members"
        )


class TestStripMarkdown:
    def test_heading_markers_dropped(self):
        assert strip_markdown("## The weekly wrap") == "The weekly wrap"

    def test_bullet_markers_dropped(self):
        assert strip_markdown("* one thing\n- two things\n+ three things") == "one thing\ntwo things\nthree things"

    def test_numbered_list_markers_dropped(self):
        assert strip_markdown("1. first\n2) second") == "first\nsecond"

    def test_emphasis_dropped(self):
        assert strip_markdown("**bold** and *ital* and _under_") == "bold and ital and under"

    def test_underscores_inside_a_word_survive(self):
        assert strip_markdown("tts_job_id is set") == "tts_job_id is set"

    def test_table_separator_rows_dropped(self):
        assert strip_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |") == "a, b\n1, 2"

    def test_blockquote_marker_dropped(self):
        assert strip_markdown("> quoted line") == "quoted line"


class TestCutFooter:
    BODY = "The market did a thing. " * 20

    def test_cuts_at_unsubscribe_in_the_back(self):
        text = self.BODY + "\nYou can unsubscribe from this list at any time.\nHere is junk."
        assert cut_footer(text) == self.BODY.rstrip()

    def test_cuts_at_you_are_receiving_this(self):
        text = self.BODY + "\nYou are receiving this email because you subscribed."
        assert cut_footer(text) == self.BODY.rstrip()

    def test_cuts_at_copyright(self):
        text = self.BODY + "\nCopyright 2026 Ledger Media Group. All rights reserved."
        assert cut_footer(text) == self.BODY.rstrip()

    def test_cuts_at_sent_to_address(self):
        text = self.BODY + "\nThis email was sent to you because you asked."
        assert cut_footer(text) == self.BODY.rstrip()

    def test_leaves_early_marker_alone(self):
        text = "Unsubscribing from cable saved me money, and here is how I did it. " * 4
        assert cut_footer(text) == text

    def test_no_marker_returns_text(self):
        assert cut_footer("A perfectly normal email with no footer at all.") == (
            "A perfectly normal email with no footer at all."
        )

    def test_marker_in_a_very_long_line_is_ignored(self):
        long_line = "word " * 500 + " unsubscribe " + "more words " * 100
        assert cut_footer(long_line) == long_line


class TestBoilerplateLines:
    def test_drops_unsubscribe_nav_strip(self):
        assert drop_boilerplate_lines("Unsubscribe | Manage preferences | View in browser") == ""

    def test_drops_social_row(self):
        assert drop_boilerplate_lines("Twitter \u00b7 Facebook \u00b7 LinkedIn") == ""

    def test_drops_advertisement_label(self):
        assert is_boilerplate_line("ADVERTISEMENT")

    def test_drops_sponsor_prefixed_line(self):
        assert is_boilerplate_line("Sponsor: Ship your next side project on Cloudhost")

    def test_drops_quote_attribution(self):
        assert is_boilerplate_line("On Feb 9, 2026, at 4:11 PM, Priya wrote:")

    def test_drops_separator_only_line(self):
        assert is_boilerplate_line("--------")

    def test_keeps_real_prose(self):
        assert not is_boilerplate_line("The bond market sold off for a fourth session.")

    def test_keeps_long_line_that_mentions_unsubscribe(self):
        line = " ".join(["the columnist argued that unsubscribe rates are the only honest metric"] * 4)
        assert len(line) > 200
        assert not is_boilerplate_line(line)


class TestStripEmoji:
    def test_removes_emoji_and_dingbats(self):
        assert strip_emoji("\U0001f4e0 Sponsor news \u2728 and \u2713 done") == " Sponsor news  and  done"

    def test_keeps_normal_punctuation(self):
        assert strip_emoji("Cost: $40 (net) [see]") == "Cost: $40 (net) [see]"


class TestUnflow:
    def test_joins_hard_wrapped_paragraph(self):
        flowed = "Quick one this week. I spent ten days\nwith the new recorder and it\nis good.\n\nSecond para."
        assert unflow_plain_text(flowed) == (
            "Quick one this week. I spent ten days with the new recorder and it is good.\n\nSecond para."
        )

    def test_keeps_list_structure(self):
        flowed = "- one thing\n- two things\n\nprose here\nwrapped prose"
        assert unflow_plain_text(flowed) == "- one thing\n- two things\n\nprose here wrapped prose"


class TestCollapseWhitespace:
    def test_collapses_spaces_and_blank_runs(self):
        assert collapse_whitespace("a   b\n\n\n\n\nc  ") == "a b\n\nc"

    def test_keeps_line_breaks_inside_a_paragraph(self):
        assert collapse_whitespace("line one\nline two\n\nnext para") == "line one\nline two\n\nnext para"


class TestMakeDescription:
    def test_short_text_passes_through(self):
        assert make_description("Short and sweet.") == "Short and sweet."

    def test_cuts_on_sentence_boundary(self):
        text = "First sentence is short. " + "Second sentence runs on and on for a while. " * 10
        assert make_description(text, length=64) == "First sentence is short."

    def test_elides_a_long_first_sentence(self):
        text = "w" * 400
        description = make_description(text, length=40)
        assert len(description) <= 41
        assert description.endswith("…")


class TestDecodePartBytes:
    def test_declared_charset(self):
        assert decode_part_bytes("caf\xe9".encode("latin-1"), "iso-8859-1") == "café"

    def test_wrong_charset_falls_back_to_utf8(self):
        assert decode_part_bytes("café".encode("utf-8"), "iso-8859-1") == "café"

    def test_unknown_8bit_is_treated_as_utf8(self):
        assert decode_part_bytes("café".encode("utf-8"), "unknown-8bit") == "café"

    def test_bogus_charset_name_does_not_raise(self):
        assert decode_part_bytes(b"plain ascii", "x-not-a-charset") == "plain ascii"

    def test_empty_payload(self):
        assert decode_part_bytes(b"", "utf-8") == ""


class TestCleanEmailHtml:
    def test_removes_tracking_pixel_without_alt(self):
        cleaned = clean_email_html('<p>Hi</p><img src="https://track.example/o" width="1" height="1" alt="">')
        assert "track.example" not in cleaned

    def test_keeps_short_alt_text(self):
        cleaned = clean_email_html('<img src="a.jpg" alt="The new warehouse">')
        assert "The new warehouse" in cleaned

    def test_drops_long_alt_text(self):
        cleaned = clean_email_html(f'<img src="a.jpg" alt="{"long " * 30}">')
        assert "long" not in cleaned

    def test_removes_hidden_preheader(self):
        cleaned = clean_email_html(
            '<div class="preheader" style="display:none">secret preview</div><p>body</p>'
        )
        assert "secret preview" not in cleaned
        assert "body" in cleaned

    def test_removes_unsubscribe_anchor(self):
        cleaned = clean_email_html('<p><a href="https://e.com/u">Unsubscribe</a></p><p>real text</p>')
        assert "Unsubscribe" not in cleaned
        assert "real text" in cleaned

    def test_removes_call_to_action_anchor(self):
        cleaned = clean_email_html('<p><a href="https://e.com/x">Read more \u2192</a> and keep going</p>')
        assert "Read more" not in cleaned
        assert "keep going" in cleaned

    def test_removes_button_styled_anchor(self):
        cleaned = clean_email_html(
            '<table><tr><td bgcolor="#111"><a href="https://shop.example/new" style="color:#fff">'
            'SHOP NEW ARRIVALS</a></td></tr></table><p>real prose</p>'
        )
        assert "SHOP NEW ARRIVALS" not in cleaned
        assert "real prose" in cleaned

    def test_keeps_ordinary_link_in_body_background(self):
        cleaned = clean_email_html(
            '<html><body bgcolor="#f4f4f7"><p>see <a href="https://e.com/a">the market letter</a> today</p></body></html>'
        )
        assert "the market letter" in cleaned

    def test_removes_conditional_comments_and_mso(self):
        cleaned = clean_email_html("<!--[if mso]><p>outlook only</p><![endif]--><p>real</p>")
        assert "outlook only" not in cleaned
        assert "real" in cleaned

    def test_removes_sponsor_and_footer_classes(self):
        cleaned = clean_email_html(
            '<div class="sponsor">Sponsored by Acme</div><div class="footer">bye</div><p>story</p>'
        )
        assert "Sponsored by Acme" not in cleaned
        assert "bye" not in cleaned
        assert "story" in cleaned

    def test_removes_quoted_reply(self):
        cleaned = clean_email_html("<p>New text</p><blockquote>old text</blockquote>")
        assert "old text" not in cleaned
        assert "New text" in cleaned


class TestHtmlToText:
    def test_falls_back_when_trafilatura_finds_nothing(self):
        html = "<div><p>" + " ".join(f"sentence number {i}" for i in range(40)) + "</p></div>"
        text, extractor = html_to_text(html)
        assert extractor in {"trafilatura", "readability", "html2text"}
        assert "sentence number 39" in text

    def test_empty_html(self):
        assert html_to_text("") == ("", "none")


class TestPlainTextToSpeakable:
    def test_unflows_and_strips_footer(self):
        text = "Hello there.\n\nThis is the body of the note, which is\nwrapped by the sender.\n\n--\nSent with Ghost."
        assert plain_text_to_speakable(text) == "Hello there.\n\nThis is the body of the note, which is wrapped by " \
            "the sender."

    def test_unescapes_html_entities(self):
        assert plain_text_to_speakable("5 &gt; 3 &amp; 2 &lt; 4") == "5 > 3 & 2 < 4"


class TestMakeSpeakableEndToEnd:
    def test_empty_input(self):
        assert make_speakable("") == ""

    def test_full_pass_over_a_hostile_newsletter(self):
        raw = (
            "\u200b\u200b\u200bThe lead story is about ports.\u200b\u200b\n\n"
            "Sponsor: Acme Cloud keeps this newsletter free. \U0001f4a5\n\n"
            "Read more at https://acme.example/coupon20\n\n"
            "You are receiving this email because you signed up.\n"
            "Unsubscribe | Manage preferences\n"
            "Copyright 2026 Ledger Media. All rights reserved."
        )
        assert make_speakable(raw) == "The lead story is about ports."
