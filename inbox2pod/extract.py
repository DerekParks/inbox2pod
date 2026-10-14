"""
Turn raw email bytes into clean speakable text.

Newsletter HTML is hostile: layout tables, tracking pixels, invisible
preheaders, sponsor blocks and unsubscribe footers. Everything here exists to
get that down to prose a TTS engine can read aloud.

Run directly to see what an email would sound like:

    python -m inbox2pod.extract tests/fixtures/*.eml
    python -m inbox2pod.extract --bakeoff tests/fixtures
"""

import argparse
import glob
import html as html_lib
import re
import sys
import unicodedata
from dataclasses import dataclass
from email import message_from_bytes, policy
from pathlib import Path

import ftfy
import html2text
import trafilatura
from bs4 import BeautifulSoup, Comment, UnicodeDammit
from readability import Document

from inbox2pod.config import Config
from inbox2pod.utils import sha256_hex

MIN_IMAGE_ALT_LENGTH = 80
MAX_BOILERPLATE_LINE_LENGTH = 200
MIN_LINK_LINE_CHARS = 12
SENTENCE_CUT_MIN_RATIO = 0.3
# A text/plain body this much longer than the HTML extraction wins
PLAIN_TEXT_PREFERRED_RATIO = 1.5

# Elements that never carry spoken content
_STRIP_TAGS = (
    "script",
    "style",
    "head",
    "title",
    "meta",
    "link",
    "noscript",
    "svg",
    "iframe",
    "video",
    "audio",
    "canvas",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "map",
    "area",
    "base",
    "template",
    "blockquote",
)

# class/id names that mark chrome rather than content
_NOISE_CLASS = re.compile(
    r"(?:^|[-_\s])(?:"
    r"preheader|preview[-_]?text|hidden[-_]?(?:text|div|content)|"
    r"footer|footnotes|legal|unsubscribe|subscription|sign[-_]?off|"
    r"view[-_]?(?:in[-_]?browser|browser|online)|web[-_]?version|"
    r"social|share[-_]?(?:icons?|links?|bar|row)?|follow[-_]?(?:us|me)|icons?|"
    r"header[-_]?(?:logo|nav)|nav(?:bar|igation)?|sidebar|menu|"
    r"download[-_]?(?:app|our[-_]?app)|get[-_]?the[-_]?app|"
    r"promo(?:tional)?|coupon|survey|poll|cta|button|disclosure|"
    r"disclaimer|copyright|powered[-_]?by|archive[-_]?links?|"
    r"sponsor(?:s|ship)?|discussion|comments?(?:[-_]?section)?|related|recommended"
    r")(?:$|[-_\s])",
    re.IGNORECASE,
)

# Call-to-action buttons: link text that is pure button chrome
_CTA_ANCHOR_TEXT = re.compile(
    r"^(?:read\s+more|learn\s+more|shop\s+(?:now|the\s+shop|now)|click\s+here|sign\s+up|"
    r"subscribe(?:\s+now|\s+free)?|register|view\s+(?:all|the\s+shop)|see\s+all|full\s+story|"
    r"read\s+(?:the\s+)?(?:full|entire)\s+(?:story|article|post|letter)|buy\s+now|order\s+now|"
    r"get\s+(?:the\s+)?app|donate|support\s+us|reply|forward|share)$",
    re.IGNORECASE,
)
CTA_ANCHOR_TEXT_MAX_LENGTH = 40
_BUTTON_STYLE = re.compile(r"background|border-radius|btn|button", re.IGNORECASE)

# A line that was just a button pointing at a link, once the link is gone
_CTA_LEAD_IN = re.compile(
    r"(?:read(?:\s+more)?|learn\s+more|click\s+here|sign\s+up|subscribe|view|see|shop|buy|get|more|details|info)"
    r"(?:\s+(?:at|in|for|to|on|here|this|our|the|a|now|today))*",
    re.IGNORECASE,
)

# inline styles that make text invisible to a human but visible to a parser
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|"
    r"color\s*:\s*(?:transparent|rgba\(0,\s*0,\s*0,\s*0\))|"
    r"(?:width|height|max-height)\s*:\s*[01]px|"
    r"mso-hide\s*:\s*all|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em)?",
    re.IGNORECASE,
)

_LINK_TEXT_NOISE = re.compile(
    r"unsubscribe|manage\s+(?:your\s+)?preferences|view\s+(?:this|it)\s+in\s+(?:a\s+)?browser|"
    r"read\s+(?:this|online)|view\s+(?:online|this\s+email\s+online)|web\s+version|"
    r"update\s+preferences|email\s+preferences|forward\s+to\s+a\s+friend",
    re.IGNORECASE,
)

_URL = re.compile(r"(?:https?://|ftp://|www\.)\S+", re.IGNORECASE)
_BARE_DOMAIN_WITH_PATH = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.){1,4}"
    r"(?:com|net|org|io|co|dev|ai|app|xyz|news|daily|weekly|tv|me|gg|sh|so|ly|to|fm|be|edu|gov)"
    r"(?:/\S*|\.[a-z]{2})?\b",
    re.IGNORECASE,
)
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\((?:[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_BARE_ANGLE_URL = re.compile(r"<\s*(?:https?://|mailto:)\s*[^>]*>", re.IGNORECASE)
_EMAIL_ADDRESS = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e\u2060-\u2064]")
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, symbols extension
    "\U00002600-\U000027bf"  # misc symbols and dingbats
    "\U00002b00-\U00002bff"  # arrows, squares, stars
    "\U00002190-\U000021ff"  # arrows
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U0001f900-\U0001f9ff"
    "]"
)

_TTS_SERVICES = (
    r"substack|beehiiv|mailchimp|buttondown|convertkit|activecampaign|brevo|sendinblue|"
    r"klaviyo|constant\s?contact|ghost|postmark|sendgrid|aws\s+ses|hubspot|mailerlite|squidfin"
)

# Markers strong enough to end the article where they appear
FOOTER_MARKERS = re.compile(
    r"(?:"
    r"unsubscrib(?:e|ing|ption)|"
    r"manage\s+(?:your\s+)?(?:preferences|subscription|profile|email)|"
    r"email\s+preferences|notification\s+settings|update\s+preferences|"
    r"view\s+(?:this|the)?\s*(?:email\s+)?(?:in|on)\s+(?:your\s+)?(?:web\s+)?browser|"
    r"view\s+this\s+email\s+online|read\s+(?:this|the)\s+(?:email\s+)?online|open\s+in\s+(?:your\s+)?browser|"
    r"(?:you|we)\s*(?:'?re|\s+are)\s+receiv(?:ing|ed)\s+this\s+(?:email|message)|"
    r"you\s+(?:got|received)\s+this\s+(?:email|message)|"
    r"if\s+you(?:'?re|\s+are)\s+having\s+(?:trouble|difficulty)\s+(?:seeing|viewing|reading)|"
    r"(?:this\s+)?(?:email|message)\s+was\s+sent\s+to\b|"
    r"(?:sent|delivered|powered)\s+by\s+(?:" + _TTS_SERVICES + r")|"
    r"copyright|all\s+rights\s+reserved|©|"
    r"(?:our|the|contact)\s+(?:us\s+)?(?:physical\s+|mailing\s+)?address|"
    r"regardless\s+of\s+your\s+subscription"
    r")",
    re.IGNORECASE,
)

# Phrases that make a whole short line disposable, wherever it appears
_BOILERPLATE_PREFIX = re.compile(
    r"^\s*[•|\-–—·*\s\u00a0]*(?:"
    r"unsubscrib(?:e|ing|ption)|manage\s+(?:your\s+)?(?:preferences|subscription|profile)|"
    r"update\s+(?:your\s+)?(?:email\s+)?preferences|email\s+preferences|subscription\s+options|"
    r"view\s+(?:this|the|it)\s+(?:email\s+)?(?:in|on)\s+(?:your\s+)?(?:web\s+)?browser|"
    r"view\s+online|web\s+version|read\s+online|open\s+in\s+(?:your\s+)?browser|"
    r"(?:you|we)\s*(?:'?re|\s+are)\s+receiv(?:ing|ed)\s+this|"
    r"(?:this\s+)?(?:email|message)\s+(?:was|is)\s+(?:sent|brought)\s+to\s+you|"
    r"(?:sent|delivered|powered)\s+by\s+(?:" + _TTS_SERVICES + r")|"
    r"(?:sent|written|published|delivered)\s+with\s+[A-Za-z][\w.]*|"
    r"copyright|©|all\s+rights\s+reserved|"
    r"(?:ad)?vertis[ei]ment|sponsored\s+(?:content|message|post)|"
    r"(?:follow|connect\s+with|find|like)\s+(?:us|me)\s+(?:on|at)\b|"
    r"(?:download|get)\s+(?:the|our)\s+app\b|"
    r"click\s+here\s+to\s+(?:unsubscribe|manage|read|view|see)|"
    r"(?:having|had)\s+(?:trouble|problems|difficulty)\s+(?:seeing|viewing|reading|hearing)|"
    r"forward(?:ed)?\s+this\s+(?:email|message)|share\s+this\s+(?:email|message|post)|"
    r"archiv(?:e|es)\s+(?:of\s+)?(?:past|previous)\s+(?:issues|newsletters)|"
    r"you\s+are\s+getting\s+this\s+email|our\s+mailing\s+address|"
    r"sponsor(?:s|ship)?\s*[:\-]|this\s+(?:email|message|newsletter)\s+is\s+sponsored|"
    r"on\s+[^.\n]{3,60}\bwrote\s*:$|-{2,}\s*(?:forwarded|original)\s+message"
    r")",
    re.IGNORECASE,
)

_SOCIAL_ROW = re.compile(
    r"^\s*[A-Za-z][A-Za-z .']{0,20}"
    r"(?:\s*(?:[|•·,;&+/]|\band\b|\bis\b)\s*[A-Za-z][A-Za-z .']{0,20})+\s*[.]?\s*$"
)
_SOCIAL_WORDS = {
    "twitter", "x", "facebook", "fb", "instagram", "linkedin", "youtube", "tiktok", "reddit",
    "bluesky", "threads", "mastodon", "whatsapp", "telegram", "rss", "podcasts", "podcast",
    "apple", "spotify", "soundcloud", "newsletter", "subscribe", "unsubscribe",
    "share", "follow", "forward", "email", "shop", "store", "merch", "donate", "support",
}
_MARKDOWN_TABLE_ROW = re.compile(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|?\s*$")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n|\n-{2,}[ \t]*\n")
_LIST_ITEM_LINE = re.compile(r"^(?:[-*+\u2022]|\d+[.)])\s+")
_SEPARATOR_ONLY_LINE = re.compile(r"^[\s|•·\-–—=_*+>#]+$")

_QUOTE_REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2032": "'",
    "\u2033": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2011": "-",
    "\u2026": "...",
}


@dataclass
class ExtractionResult:
    """What came out of one email."""

    text: str
    description: str
    extractor: str
    ok: bool


# ---------------------------------------------------------------------------
# MIME
# ---------------------------------------------------------------------------


def decode_part_bytes(payload: bytes, charset: str | None) -> str:
    """Decode a MIME part's bytes without ever raising on a bad charset."""
    if not payload:
        return ""

    if charset:
        charset = charset.strip().lower()
        if charset in ("unknown-8bit", "x-unknown", "default", "ansi_x3.4-1968"):
            charset = None

    if charset:
        try:
            return ftfy.fix_text(payload.decode(charset))
        except (LookupError, UnicodeDecodeError):
            pass

    recovered = UnicodeDammit(
        payload, known_definite_encodings=[charset] if charset else [], user_encodings=["utf-8"]
    ).unicode_markup
    return ftfy.fix_text(recovered or "")


def _is_attachment(part) -> bool:
    if str(part.get("Content-Disposition") or "").lower().startswith("attachment"):
        return True
    return bool(part.get_filename())


def collect_text_parts(msg) -> dict[str, str]:
    """Collect the text/html and text/plain bodies of a message.

    Attachments are skipped and nested message/rfc822 parts (forwarded mail)
    are walked too. Multiple parts of the same type are concatenated.
    """
    found: dict[str, list[str]] = {"text/html": [], "text/plain": []}

    for part in msg.walk():
        if part.is_multipart() or _is_attachment(part):
            continue

        content_type = part.get_content_type()
        if content_type not in found:
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            raw = part.get_payload()
            text = raw if isinstance(raw, str) else ""
        else:
            text = decode_part_bytes(payload, part.get_content_charset())

        if text.strip():
            found[content_type].append(text)

    return {kind: "\n\n".join(parts) for kind, parts in found.items() if parts}


# ---------------------------------------------------------------------------
# HTML cleaning and extraction
# ---------------------------------------------------------------------------


def _make_soup(markup: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


def _is_button_chrome(anchor) -> bool:
    text = anchor.get_text(" ", strip=True)
    if not text:
        return False
    if _LINK_TEXT_NOISE.search(text):
        return True
    if len(text) > CTA_ANCHOR_TEXT_MAX_LENGTH:
        return False

    normalized = re.sub(r"[^\w\s]", "", text).strip()
    return bool(_CTA_ANCHOR_TEXT.fullmatch(normalized)) or _is_styled_button(anchor)


def _is_styled_button(anchor) -> bool:
    """Email buttons are a link painted with a background or rounded corners."""
    parent = anchor.parent
    if parent is None or parent.name in ("html", "body"):
        return bool(_BUTTON_STYLE.search(anchor.get("style") or ""))

    style = f"{anchor.get('style') or ''} {parent.get('style') or ''}"
    return bool(_BUTTON_STYLE.search(style) or parent.get("bgcolor"))


def clean_email_html(html: str) -> str:
    """Drop the parts of newsletter HTML that are never spoken out loud."""
    soup = _make_soup(html)

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag in soup.find_all(style=_HIDDEN_STYLE):
        tag.decompose()

    for tag in soup.find_all(class_=_NOISE_CLASS) + soup.find_all(id=_NOISE_CLASS):
        if not getattr(tag, "decomposed", False):
            tag.decompose()

    for image in soup.find_all("img"):
        alt = " ".join((image.get("alt") or "").split())
        if alt and len(alt) <= MIN_IMAGE_ALT_LENGTH and not _LINK_TEXT_NOISE.search(alt):
            image.replace_with(f" {alt} ")
        else:
            image.decompose()

    for anchor in soup.find_all("a"):
        if _is_button_chrome(anchor):
            anchor.decompose()

    return str(soup)


def extract_with_trafilatura(html: str) -> str:
    """Boilerplate removal via trafilatura."""
    try:
        return (
            trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                include_links=False,
                include_images=False,
                favor_recall=True,
                fast=True,
            )
            or ""
        )
    except Exception as e:
        print(f"trafilatura failed: {e}")
        return ""


def extract_with_readability(html: str) -> str:
    """Article extraction via readability-lxml, converted with html2text."""
    try:
        summary = Document(html, url="https://example.com/").summary()
    except Exception as e:
        print(f"readability failed: {e}")
        return ""
    return html_to_text_with_html2text(summary if isinstance(summary, str) else str(summary))


def html_to_text_with_html2text(html: str) -> str:
    """The dumb path: always produces something."""
    converter = html2text.HTML2Text(bodywidth=0)
    converter.ignore_links = True
    converter.ignore_images = True
    converter.ignore_emphasis = True
    converter.body_width = 0
    converter.unicode_snob = True
    converter.skip_internal_links = True
    converter.ignore_mailto_links = True
    try:
        return converter.handle(html) or ""
    except Exception as e:
        print(f"html2text failed: {e}")
        return ""


def html_to_soup_text(html: str) -> str:
    """Last resort: strip every tag and keep whatever text is left."""
    return _make_soup(html).get_text(" ", strip=True)


HTML_EXTRACTORS = (
    ("trafilatura", extract_with_trafilatura),
    ("readability", extract_with_readability),
    ("html2text", html_to_text_with_html2text),
)


def html_to_text(html: str) -> tuple[str, str]:
    """Convert email HTML to speakable text with the best extractor that worked.

    Returns:
        tuple[str, str]: The text and the name of the extractor that produced it
    """
    cleaned = clean_email_html(html)
    for name, extractor in HTML_EXTRACTORS:
        text = make_speakable(extractor(cleaned))
        if len(text) >= Config.MIN_TEXT_LENGTH:
            return text, name

    text = make_speakable(html_to_soup_text(cleaned))
    return (text, "soup") if text else ("", "none")


def unflow_plain_text(text: str) -> str:
    """Join hard-wrapped lines back into paragraphs, keeping list structure."""
    blocks = []
    for block in re.split(_PARAGRAPH_BREAK, text):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        is_list = any(_LIST_ITEM_LINE.match(line) for line in lines)
        blocks.append("\n".join(lines) if is_list else " ".join(lines))
    return "\n\n".join(blocks)


def plain_text_to_speakable(text: str) -> str:
    """Normalize a text/plain newsletter body: unflow it, unescape it, speak it."""
    return make_speakable(unflow_plain_text(html_lib.unescape(text)))


# ---------------------------------------------------------------------------
# Speakability pass
# ---------------------------------------------------------------------------


def fix_mojibake(text: str) -> str:
    """Repair broken encodings and normalize compatibility characters."""
    text = ftfy.fix_text(text)
    text = unicodedata.normalize("NFKC", text)
    for unicode_char, ascii_char in _QUOTE_REPLACEMENTS.items():
        text = text.replace(unicode_char, ascii_char)
    return text


def strip_zero_width(text: str) -> str:
    """Remove the invisible characters email preheaders are built from."""
    return _ZERO_WIDTH.sub("", text)


def strip_emoji(text: str) -> str:
    """Remove emoji and dingbats a TTS engine reads as noise."""
    return _EMOJI.sub("", text)


def _remove_url_markup(line: str) -> str:
    line = _MARKDOWN_IMAGE.sub("", line)
    line = _MARKDOWN_LINK.sub(lambda match: match.group(1), line)
    line = _BARE_ANGLE_URL.sub("", line)
    line = _URL.sub("", line)
    line = _EMAIL_ADDRESS.sub("", line)
    line = _BARE_DOMAIN_WITH_PATH.sub("", line)
    return re.sub(r"[\(\[<]\s*[\)\]>]", "", line)


def strip_urls(text: str) -> str:
    """Keep link text, never the href.

    A line that was mostly a link has nothing left worth speaking, so it goes;
    a line that merely mentions a link keeps its prose minus the dangling
    "Read more at:" punctuation.
    """
    kept = []
    for line in text.split("\n"):
        without_urls = _remove_url_markup(line)
        if without_urls != line:
            remainder = without_urls.strip()
            normalized = re.sub(r"[^\w\s]", "", remainder).strip()
            if (
                len(remainder) < MIN_LINK_LINE_CHARS
                or remainder.endswith((":", "-", "\u2013", "\u2014"))
                or _CTA_LEAD_IN.fullmatch(normalized)
            ):
                continue
            without_urls = re.sub(r"[ \t]{2,}", " ", without_urls).strip()
        kept.append(without_urls)

    return "\n".join(kept)


def strip_markdown(text: str) -> str:
    """Turn markdown leftovers into plain sentences."""
    lines = []
    for line in text.split("\n"):
        if _MARKDOWN_TABLE_ROW.match(line):
            continue

        line = re.sub(r"^\s*#{1,6}\s+", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^\s*\d+[.)]\s+", "", line)
        line = re.sub(r"^\s*[-*+•]\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"\1", line)
        line = re.sub(r"(?<![\w_])__([^_\n]+?)__(?![\w_])", r"\1", line)
        line = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"\1", line)
        line = re.sub(r"`([^`\n]+?)`", r"\1", line)

        if "|" in line:
            line = ", ".join(cell.strip() for cell in line.split("|") if cell.strip())

        lines.append(line)

    return "\n".join(lines)


def cut_footer(text: str, start_fraction: float = 0.3, max_line_length: int = 400) -> str:
    """Cut everything from the first footer marker in the back of the email on."""
    threshold = len(text) * start_fraction
    offset = 0

    for line in text.split("\n"):
        if offset >= threshold and len(line) <= max_line_length and FOOTER_MARKERS.search(line):
            return text[:offset].rstrip()
        offset += len(line) + 1

    return text


def is_boilerplate_line(line: str) -> bool:
    """Whether a short line is newsletter chrome rather than content."""
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_BOILERPLATE_LINE_LENGTH:
        return False

    if _SEPARATOR_ONLY_LINE.match(stripped):
        return True

    if _BOILERPLATE_PREFIX.match(stripped):
        return True

    if _SOCIAL_ROW.match(stripped):
        tokens = re.split(r"[|•·,;&+/]|\band\b|\bis\b", stripped)
        words = [word.strip().lower().rstrip(".") for token in tokens for word in token.split()]
        words = [word for word in words if word]
        return bool(words) and all(word in _SOCIAL_WORDS for word in words)

    return False


def drop_boilerplate_lines(text: str) -> str:
    """Remove lines that are entirely newsletter chrome, wherever they sit."""
    return "\n".join(line for line in text.split("\n") if not is_boilerplate_line(line))


def dedupe_repeated_lines(text: str, max_repeats: int = 2) -> str:
    """Collapse runs of an identical line (icon rows, repeated nav)."""
    result = []
    previous = None
    repeats = 0

    for line in text.split("\n"):
        key = line.strip().lower()
        if key and key == previous:
            repeats += 1
            if repeats > max_repeats:
                continue
        else:
            previous = key
            repeats = 1
        result.append(line)

    return "\n".join(result)


def collapse_whitespace(text: str) -> str:
    """Squeeze runs of spaces and blank lines, keeping paragraph breaks."""
    blocks = []
    for block in re.split(r"\n[ \t]*\n", text.replace("\r", "\n")):
        lines = [" ".join(line.split()) for line in block.split("\n")]
        lines = [line for line in lines if line]
        if lines:
            blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def make_speakable(text: str) -> str:
    """Run the whole speakability pass over extracted text."""
    if not text:
        return ""

    text = fix_mojibake(text)
    text = strip_zero_width(text)
    text = strip_emoji(text)
    text = strip_markdown(text)
    text = cut_footer(text)
    text = drop_boilerplate_lines(text)
    text = dedupe_repeated_lines(text)
    text = strip_urls(text)
    return collapse_whitespace(text)


def make_description(text: str, length: int | None = None) -> str:
    """Build the feed description from the first sentences of the episode."""
    length = length or Config.DESCRIPTION_LENGTH
    flat = " ".join(text.split())
    if len(flat) <= length:
        return flat

    window = flat[:length]
    sentence_end = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence_end >= length * SENTENCE_CUT_MIN_RATIO:
        return window[: sentence_end + 1]

    cut = window.rfind(" ")
    return (window[:cut] if cut > 0 else window).rstrip(" ,;:-") + "…"


def extract_email(raw_bytes: bytes) -> ExtractionResult:
    """Extract speakable text from a raw RFC822 email.

    HTML wins when it produced enough text; the text/plain alternative and the
    bare soup are fallbacks so a weird newsletter still becomes an episode.
    """
    msg = message_from_bytes(raw_bytes, policy=policy.default)
    parts = collect_text_parts(msg)

    html = parts.get("text/html", "")
    plain_text = plain_text_to_speakable(parts["text/plain"]) if parts.get("text/plain") else ""

    if html:
        text, extractor = html_to_text(html)
        if len(plain_text) > len(text) * PLAIN_TEXT_PREFERRED_RATIO:
            return _result(plain_text, "plain")
        return _result(text, extractor)

    if parts.get("text/plain"):
        return _result(plain_text_to_speakable(parts["text/plain"]), "plain")

    return ExtractionResult(text="", description="", extractor="none", ok=False)


def _result(text: str, extractor: str) -> ExtractionResult:
    return ExtractionResult(
        text=text,
        description=make_description(text),
        extractor=extractor,
        ok=len(text) >= Config.MIN_TEXT_LENGTH,
    )


def raw_fingerprint(raw_bytes: bytes) -> str:
    """Short sha256 of a raw message, for fixture bookkeeping."""
    return sha256_hex(raw_bytes)[:16]


def _bakeoff(directory: str) -> int:
    paths = sorted(Path(directory).rglob("*.eml"))
    if not paths:
        print(f"No .eml files under {directory}")
        return 1

    print(f"{'fixture':34.34} {'chosen':12} {'chars':>7} {'words':>7}  candidates")
    print("-" * 110)
    for path in paths:
        msg = message_from_bytes(path.read_bytes(), policy=policy.default)
        parts = collect_text_parts(msg)
        html = parts.get("text/html", "")
        plain = parts.get("text/plain", "")

        candidates = {}
        if html:
            cleaned = clean_email_html(html)
            for name, extractor in HTML_EXTRACTORS:
                candidates[name] = len(make_speakable(extractor(cleaned)))
        if plain:
            candidates["plain"] = len(plain_text_to_speakable(plain))

        chosen = extract_email(path.read_bytes())
        print(
            f"{path.name:34.34} {chosen.extractor:12} {len(chosen.text):>7} {len(chosen.text.split()):>7}  "
            + " ".join(f"{name}={length}" for name, length in candidates.items())
        )
    return 0


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show the speakable text extracted from .eml files")
    parser.add_argument("files", nargs="*", help="Path(s) or glob(s) of .eml files")
    parser.add_argument("--bakeoff", metavar="DIR", help="Compare every extractor over a directory of .eml files")
    parser.add_argument("--full", action="store_true", help="Print the whole extracted text, not just the head")
    args = parser.parse_args(argv)

    if args.bakeoff:
        return _bakeoff(args.bakeoff)

    if not args.files:
        parser.error("give at least one .eml file, or use --bakeoff DIR")

    paths = [p for pattern in args.files for p in sorted(glob.glob(pattern))] or args.files
    for path in paths:
        result = extract_email(Path(path).read_bytes())
        header = f"{path}  [{result.extractor}]  {len(result.text)} chars  ok={result.ok}"
        print(f"\n{'=' * len(header)}\n{header}\n{'=' * len(header)}")
        print(result.text if args.full else result.text[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
