"""Generate the .eml fixture corpus.

These are synthetic newsletters built to look like the real thing: table
layouts, hidden preheaders, tracking pixels, sponsor blocks, unsubscribe
footers, broken charsets. Real newsletters exported from Gmail go in the same
directory (sanitize recipient addresses first) and every extraction bug found
in the wild becomes a fixture here.

    uv run python tests/make_fixtures.py [--force]
"""

import quopri
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

PREHEADER = "The one thing nobody is saying about the market this week." + "&#8203;" * 40

SHELL_HEAD = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset={charset}" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
<style type="text/css">
  body {{ margin:0; padding:0; }}
  .preheader {{ color:transparent; display:none!important; font-size:1px; max-height:0; max-width:0; }}
</style>
</head>
<body bgcolor="#f4f4f7" style="margin:0;padding:0;">
<div class="preheader">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f4f4f7">
<tr><td align="center" style="padding:16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" bgcolor="#ffffff" class="wrapper">
<tr><td class="header-logo" style="padding:20px;">
  <a href="https://example.com/"><img src="https://cdn.example.com/logo.png" width="180" alt="The Daily Ledger" /></a>
</td></tr>
"""

SHELL_FOOT = """<tr><td class="footer" style="padding:24px;font-size:12px;color:#9aa;line-height:18px;">
  <p>You are receiving this email because you subscribed to The Daily Ledger.</p>
  <p><a href="https://example.com/unsubscribe?t=REDACTEDTOKEN">Unsubscribe</a> &nbsp;|&nbsp;
     <a href="https://example.com/preferences?t=REDACTEDTOKEN">Manage preferences</a> &nbsp;|&nbsp;
     <a href="https://example.com/view/{mid}">View this email in your browser</a></p>
  <p>The Daily Ledger, 1234 News Way, Suite 100, New York, NY 10001</p>
  <p>Copyright &#169; 2026 Ledger Media Group. All rights reserved.</p>
  <p><a href="https://twitter.com/example">Twitter</a> &#183;
     <a href="https://facebook.com/example">Facebook</a> &#183;
     <a href="https://linkedin.com/company/example">LinkedIn</a></p>
  <img src="https://track.example.com/open/{mid}" width="1" height="1" alt="" border="0" />
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def shell(body: str, title: str = "The Daily Ledger", charset: str = "utf-8", mid: str = "fixture") -> str:
    return SHELL_HEAD.format(charset=charset, title=title, preheader=PREHEADER) + body + SHELL_FOOT.format(mid=mid)


def paragraph(text: str) -> str:
    return f'<p style="font-family:Georgia,serif;font-size:16px;line-height:24px;">{text}</p>'


LONG_ARTICLE = [
    "Markets spent most of Tuesday pretending that nothing had happened, which in this cycle is its own kind "
    "of news. The index closed up eleven points, a move so small it barely qualifies as an opinion, and yet "
    "the underlying plumbing told a much less calm story.",
    "Underlying yields rose for the fourth straight session. That matters because everything in the last two "
    "years has been priced off the assumption that borrowing costs had peaked. Four straight up days in the "
    "long bond is the market starting to doubt that assumption out loud.",
    "The energy complex was the other tell. Crude sold off hard into the London close, then quietly recovered "
    "in the American afternoon. Nobody at the exchanges will say why, and the volume profile suggests a "
    "single large seller rather than a change in fundamentals.",
    "\u201cWhat we are hearing from clients is not fear, it is fatigue,\u201d said one rate strategist at a "
    "large dealer bank, speaking on condition of anonymity because he is not authorized to talk to press. "
    "\u201cEveryone wants the all-clear signal, and nobody wants to be the one who says it too early.\u201d",
    "Equity breadth, meanwhile, was ugly. Roughly a third of constituents closed lower even as the headline "
    "index rose, which is what a narrowing market looks like before it stops being a narrowing market.",
    "For the retail investor, none of this is actionable on its own. It is, however, a reminder that a "
    "portfolio built for a world of falling rates is a portfolio with one assumption holding it up, and "
    "assumptions are cheaper to test than to repair.",
    "Tomorrow brings the inflation print, and with it the first chance in six weeks for the bond market to be "
    "either vindicated or wrong in a way that everyone can agree on. Until then, eleven points.",
]

DIGEST_ITEMS = [
    ("Spotify raises prices again", "The music streamer is lifting premium pricing in fourteen markets, the "
     "second increase in eighteen months. Analysts read this as a margin story rather than a growth story: "
     "subscriber counts are fine, average revenue per user is the number being managed."),
    ("The chip export carve-out nobody asked for", "New guidance quietly exempts a category of memory "
     "hardware that was supposed to be restricted last quarter. The exemption is retroactive, which is the "
     "part worth reading twice."),
    ("Why your airline ticket got more complicated", "Basic economy was supposed to be simple. Instead it "
     "became the chassis for a dozen ancillary products, and now the airlines are experimenting with "
     "pricing the seat itself separately from the window."),
    ("A boring win for transit", "A county outside Phoenix ran a frequent bus network for a full year and "
     "ridership held. Holding is the miracle. Most experiments in American transit do not survive a "
     "budget cycle, let alone a ridership dip."),
    ("One more thing about the housing data", "The headline number was fine. The composition was not: almost "
     "all of the strength came from the south, and almost none of it from anywhere with job growth."),
]


def build_long_form() -> bytes:
    body = [
        '<tr><td style="padding:0 32px 8px;">'
        '<h1 style="font-family:Georgia,serif;font-size:28px;">'
        "The bond market is starting to say the quiet part</h1></td></tr>"
    ]
    body.append(
        '<tr><td style="padding:0 32px 16px;"><p style="color:#777;font-size:14px;">By <a '
        'href="https://example.com/authors/amy-field">Amy Field</a> &#183; 6 minute read</p></td></tr>'
    )
    body.append(
        '<tr><td style="padding:0 32px 16px;"><img src="https://cdn.example.com/bonds-hero.jpg" width="536" '
        'alt="A trading floor screen showing bond yields" /></td></tr>'
    )
    for i, para in enumerate(LONG_ARTICLE):
        body.append(f'<tr><td style="padding:0 32px 12px;">{paragraph(para)}</td></tr>')
        if i == 2:
            body.append(
                '<tr><td class="promo" style="padding:16px 32px;background:#fffbea;"><p '
                'style="font-size:13px;color:#666;"><strong>SPONSORED</strong> Our friends at Ledger Wealth '
                'will rebalance your portfolio for a flat fee. <a href="https://ads.example.com/click?id=99">'
                'Learn more</a> and get 20% off your first year.</p></td></tr>'
            )
    body.append(
        '<tr><td style="padding:16px 32px;"><p style="font-size:14px;">Read the full market letter: '
        '<a href="https://example.com/market-letter/2026-02-11">example.com/market-letter/2026-02-11</a></p>'
        "</td></tr>"
    )
    mid = make_msgid(domain="dailyledger.example")
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["Date"] = formatdate(timeval=1770750000, usegmt=True)
    msg["From"] = "Money Stuff <news@dailyledger.example>"
    msg["To"] = "RECIPIENT@example.com"
    msg["Subject"] = "The bond market is starting to say the quiet part"
    msg["List-Unsubscribe"] = "<https://example.com/unsubscribe?t=REDACTEDTOKEN>"
    msg.set_content("Read the HTML version for the full layout.")
    msg.add_alternative(shell("\n".join(body), mid=mid.strip("<>")), subtype="html")
    return msg.as_bytes()


def build_digest() -> bytes:
    rows = []
    for i, (title, blurb) in enumerate(DIGEST_ITEMS, start=1):
        rows.append(
            f"""<tr><td style="padding:18px 28px;border-bottom:1px solid #eee;">
  <p style="font-size:18px;font-weight:bold;margin:0 0 6px;">{i}. {title}</p>
  <p style="font-size:15px;line-height:22px;margin:0 0 8px;">{blurb}</p>
  <p style="font-size:13px;margin:0;"><a href="https://link.example.com/go/{i}">Read more &#8594;</a></p>
</td></tr>"""
        )
    body = "".join(
        [
            '<tr><td class="header" style="padding:20px 28px;background:#111;color:#fff;">'
            "<p style=\"font-size:22px;margin:0;\">TLDR: Today's read &#8212; 5 items, 4 minute read</p></td></tr>",
            '<tr><td style="padding:0;">',
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">',
            *rows,
            "</table></td></tr>",
            '<tr><td style="padding:20px 28px;"><p style="font-size:14px;">&#128240; Sponsor: '
            "Ship your next side project on Cloudhost. <a href=\"https://ads.example.com/cloudhost\">Use code "
            'LEDGER for $100 in credit.</a> &#128240;</p></td></tr>',
            '<tr><td class="share" style="padding:12px 28px;"><p style="font-size:13px;">'
            '<a href="https://example.com/forward">Forward to a friend</a> &#183; '
            '<a href="https://example.com/subscribe">Subscribe</a></p></td></tr>',
        ]
    )
    mid = make_msgid(domain="tldr.example")
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["Date"] = formatdate(timeval=1770836400, usegmt=True)
    msg["From"] = "TLDR <daily@tldr.example>"
    msg["To"] = "RECIPIENT@example.com"
    msg["Subject"] = "TLDR: The chip carve-out, a boring transit win, and seats by the window"
    msg.set_content("Read the HTML version for the full layout.")
    msg.add_alternative(shell(body, mid=mid.strip("<>")), subtype="html")
    return msg.as_bytes()


def build_image_heavy() -> bytes:
    tiles = "".join(
        f"""<td width="33%" align="center" style="padding:8px;">
      <img src="https://cdn.example.com/products/{i}.jpg" width="160" height="160"
           alt="Grid Sleeve Tee - ${28 + i}" />
      <p style="font-size:12px;color:#333;">Grid Sleeve Tee</p>
      <p style="font-size:12px;color:#999;">${28 + i}.00</p>
    </td>"""
        for i in range(1, 4)
    )
    body = "".join(
        [
            '<tr><td align="center" style="padding:24px;"><img src="https://cdn.example.com/spring-hero.jpg" '
            'width="536" height="300" alt="Spring 2026" /></td></tr>',
            '<tr><td align="center" style="padding:0 28px;"><table role="presentation" width="100%">'
            f"<tr>{tiles}</tr></table></td></tr>",
            '<tr><td align="center" style="padding:20px;"><!-- cta --><table role="presentation"><tr>'
            '<td bgcolor="#111" style="padding:12px 28px;border-radius:2px;"><a href="https://shop.example.com/new"'
            ' style="color:#fff;text-decoration:none;font-size:14px;">SHOP NEW ARRIVALS</a></td></tr></table>'
            "</td></tr>",
            '<tr><td style="padding:0 28px;"><p style="font-size:13px;color:#555;">Free shipping on orders '
            "over $75. Returns accepted within 30 days. See site for full details and exclusions.</p></td></tr>",
        ]
    )
    mid = make_msgid(domain="shop.example")
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["Date"] = formatdate(timeval=1770900000, usegmt=True)
    msg["From"] = "Ledger Supply Co <newarrivals@mail.shop.example>"
    msg["To"] = "RECIPIENT@example.com"
    msg["Subject"] = "Spring 2026 is here"
    msg.set_content("Spring 2026 is here. Shop new arrivals: https://shop.example.com/new")
    msg.add_alternative(shell(body, mid=mid.strip("<>")), subtype="html")
    return msg.as_bytes()


def build_plain_only() -> bytes:
    body = (
        "Hey there,\n"
        "\n"
        "Quick one this week. I spent ten days with the new version of the\n"
        "field recorder and the headline feature is the one I told them not\n"
        "to build: a physical button. A real one. You press it and it\n"
        "records, which is a radical idea in a category that spent five\n"
        "years moving everything behind a touchscreen.\n"
        "\n"
        "The battery is honest this time \u2014 19 hours measured, not 20\n"
        "advertised. The preamps are clean enough that I stopped reaching\n"
        "for my external box, which is the only compliment that counts here.\n"
        "\n"
        "Full write-up when the lab data is in. Reply and tell me what you\n"
        "want me to measure.\n"
        "\n"
        "\u2014 Sam\n"
        "\n"
        "--\n"
        "Sent with Ghost. You are receiving this email because you subscribed\n"
        "to Field Notes. Unsubscribe: https://field.example.com/u/REDACTED\n"
    )
    mid = make_msgid(domain="field.example")
    headers = "\r\n".join(
        [
            f"Message-ID: {mid}",
            "Date: " + formatdate(timeval=1770950000, usegmt=True),
            "From: Field Notes <sam@field.example>",
            "To: RECIPIENT@example.com",
            "Subject: The radical feature is a button",
            'Content-Type: text/plain; charset="utf-8"',
            "Content-Transfer-Encoding: quoted-printable",
        ]
    )
    encoded = quopri.encodestring(body.encode("utf-8")).decode("ascii")
    return (headers + "\r\n\r\n" + encoded.replace("\n", "\r\n")).encode("ascii")


def build_mojibake() -> bytes:
    """UTF-8 bytes with a header that lies about the charset."""
    body = (
        "<html><body>"
        "<p>The bank\u2019s \u201cprudent\u201d stance \u2014 and the word is doing a lot of work "
        "here \u2014 cost shareholders about \u00a340 million.</p>"
        "<p>\u201cWe got it wrong,\u201d the chair said, in the closest thing to an apology "
        "anyone has heard from a regulated institution since 2008. \u201cWe\u2019re sorry.\u201d</p>"
        + paragraph(" ".join(LONG_ARTICLE[1:3]))
        + "</body></html>"
    )
    mid = make_msgid(domain="badchar.example")
    headers = [
        f"Message-ID: {mid}",
        "Date: " + formatdate(timeval=1771000000, usegmt=True),
        "From: Ledger Finance <news@mail.badchar.example>",
        "To: RECIPIENT@example.com",
        "Subject: =?utf-8?b?VGhlIGJhbmsncyBwcnVkZW50IHN0YW5jZQ==?=",
        'Content-Type: text/html; charset="us-ascii"',
        "Content-Transfer-Encoding: 8bit",
        "",
    ]
    return ("\r\n".join(headers).encode("ascii") + b"\r\n") + body.encode("utf-8")


def build_no_message_id() -> bytes:
    body = shell(
        "".join(f'<tr><td style="padding:0 32px 12px;">{paragraph(p)}</td></tr>' for p in LONG_ARTICLE[:4]),
        mid="no-message-id",
    )
    headers = [
        "Date: " + formatdate(timeval=1771050000, usegmt=True),
        "From: Anonymous Digest <digest@noid.example>",
        "To: RECIPIENT@example.com",
        "Subject: No Message-ID here, dedupe must fall back",
        'Content-Type: text/html; charset="utf-8"',
        "Content-Transfer-Encoding: 7bit",
        "",
    ]
    return ("\r\n".join(headers)).encode("ascii") + body.encode("utf-8")


def build_tracking_only() -> bytes:
    """A newsletter that is nothing but a pixel and an unsubscribe link."""
    body = (
        '<html><body style="margin:0;">'
        '<img src="https://track.example.com/open/abc123" width="1" height="1" alt="" />'
        '<div style="display:none;font-size:1px;color:#fefefe;">&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</div>'
        '<a href="https://example.com/unsubscribe?t=REDACTED">Unsubscribe from this list</a>'
        "</body></html>"
    )
    mid = make_msgid(domain="spam.example")
    headers = [
        f"Message-ID: {mid}",
        "Date: " + formatdate(timeval=1771100000, usegmt=True),
        "From: Totally Real List <no-reply@spam.example>",
        "To: RECIPIENT@example.com",
        "Subject: (no subject)",
        'Content-Type: text/html; charset="utf-8"',
        "Content-Transfer-Encoding: 7bit",
        "",
    ]
    return ("\r\n".join(headers)).encode("ascii") + body.encode("utf-8")


def build_reply_thread() -> bytes:
    body = "".join(
        [
            '<div dir="ltr">',
            "<p>Sending along the numbers you asked for. The warehouse deal closes Thursday, "
            "so the Q3 line items move into Q4 and the comparison will look worse than it is. "
            "I would rather eat that than book it wrong, and finance agrees after looking at the "
            "carry cost on the old facility one more time.</p>",
            '<blockquote style="margin:8px 0 0 8px;padding-left:8px;border-left:1px solid #ccc">',
            "<div>On Feb 9, 2026, at 4:11 PM, Priya &lt;priya@example.com&gt; wrote:</div>",
            "<p>&gt; Can you send the warehouse numbers before Tuesday? I want to check the "
            "carry cost against the old facility before we sign.</p>",
            "<p>&gt; Also did anyone look at whether the insurance rider rolls over?</p>",
            "</blockquote>",
            "</div>",
        ]
    )
    mid = make_msgid(domain="repliers.example")
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["Date"] = formatdate(timeval=1771150000, usegmt=True)
    msg["From"] = "Marcus Delgado <marcus@example.com>"
    msg["To"] = "RECIPIENT@example.com"
    msg["Subject"] = "RE: RE: warehouse numbers"
    msg.set_content(
        "Sending along the numbers you asked for. The warehouse deal closes Thursday, so the Q3 line "
        "items move into Q4 and the comparison will look worse than it is. I would rather eat that than "
        "book it wrong, and finance agrees after looking at the carry cost on the old facility one more "
        "time.\n"
    )
    msg.add_alternative(f"<html><body>{body}</body></html>", subtype="html")
    return msg.as_bytes()


def build_attachment_and_body() -> bytes:
    body = "".join(f'<tr><td style="padding:0 32px 12px;">{paragraph(p)}</td></tr>' for p in LONG_ARTICLE[:3])
    msg = EmailMessage()
    msg["Message-ID"] = make_msgid(domain="mixed.example")
    msg["Date"] = formatdate(timeval=1771200000, usegmt=True)
    msg["From"] = "Ledger Research <research@mixed.example>"
    msg["To"] = "RECIPIENT@example.com"
    msg["Subject"] = "The quarterly, plus the appendix you asked for"
    msg.set_content("The quarterly is attached. Summary: yields up, breadth narrow, positioning crowded.")
    msg.add_alternative(shell(body, mid="attachment"), subtype="html")
    msg.add_attachment(
        b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n",
        maintype="application",
        subtype="pdf",
        filename="appendix.pdf",
    )
    return msg.as_bytes()


def build_broken_subject() -> bytes:
    """A subject with an undecodable encoded-word and no Date header."""
    body = shell(
        "".join(f'<tr><td style="padding:0 32px 12px;">{paragraph(p)}</td></tr>' for p in LONG_ARTICLE[3:6]),
        mid="broken-headers",
    )
    headers = [
        "Message-ID: <broken-headers@broken.example>",
        "From: Broken Headers =?x-unknown-charset?Q?Team?= <broken@broken.example>",
        "To: RECIPIENT@example.com",
        "Subject: =?iso-8859-1?Q?M=FClle?= newsletter #42",
        'Content-Type: text/html; charset="utf-8"',
        "Content-Transfer-Encoding: 7bit",
        "",
    ]
    return ("\r\n".join(headers)).encode("ascii") + body.encode("utf-8")


def build_forwarded_nested() -> bytes:
    """A forwarded message: outer text/html wrapper plus a nested message/rfc822."""
    inner = EmailMessage()
    inner["Message-ID"] = make_msgid(domain="original.example")
    inner["Date"] = formatdate(timeval=1771250000, usegmt=True)
    inner["From"] = "Original Author <author@original.example>"
    inner["To"] = "somebody@elsewhere.example"
    inner["Subject"] = "The forwarded essay"
    inner.set_content(" ".join(LONG_ARTICLE[2:5]))

    outer_body = (
        "<html><body><p>Forwarding this along, worth a read.</p>"
        f"<p>{' '.join(LONG_ARTICLE[5:6])}</p></body></html>"
    )
    msg = EmailMessage()
    msg["Message-ID"] = make_msgid(domain="forward.example")
    msg["Date"] = formatdate(timeval=1771253600, usegmt=True)
    msg["From"] = "A Friend <friend@example.com>"
    msg["To"] = "RECIPIENT@example.com"
    msg["Subject"] = "Fwd: The forwarded essay"
    msg.set_content("Forwarding this along, worth a read.")
    msg.add_alternative(outer_body, subtype="html")
    msg.attach(inner)
    return msg.as_bytes()


def build_substack_style() -> bytes:
    body = "".join(
        [
            '<div class="email-container" style="max-width:600px;margin:0 auto;">',
            '<div class="post-header"><h1 class="post-title" style="font-size:30px;">'
            "I unsubscribed from everything for thirty days</h1>",
            '<p class="byline" style="color:#6f6f6f;">By Riley Chen &#183; Feb 12, 2026</p></div>',
            *(f'<div class="paragraph">{paragraph(p)}</div>' for p in LONG_ARTICLE),
            '<div class="cta-box" style="background:#f7f7f5;padding:16px;"><p>Enjoyed this? '
            '<a href="https://newsletter.example.com/subscribe">Subscribe</a> to get it every Tuesday.</p></div>',
            '<div class="discussion"><p>Join the discussion in the comments section on the web.</p></div>',
            "</div>",
        ]
    )
    mid = make_msgid(domain="newsletter.example")
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["Date"] = formatdate(timeval=1771300000, usegmt=True)
    msg["From"] = "Riley Chen <riley@newsletter.example>"
    msg["To"] = "RECIPIENT@example.com"
    msg["Subject"] = "I unsubscribed from everything for thirty days"
    msg.set_content(" ".join(LONG_ARTICLE))
    msg.add_alternative(f"<html><body>{body}</body></html>", subtype="html")
    return msg.as_bytes()


FIXTURE_BUILDERS = {
    "money_stuff_longform.eml": build_long_form,
    "tldr_digest.eml": build_digest,
    "image_heavy_product.eml": build_image_heavy,
    "plain_only.eml": build_plain_only,
    "mojibake_bad_charset.eml": build_mojibake,
    "no_message_id.eml": build_no_message_id,
    "tracking_pixel_only.eml": build_tracking_only,
    "reply_thread.eml": build_reply_thread,
    "attachment_and_body.eml": build_attachment_and_body,
    "broken_headers.eml": build_broken_subject,
    "forwarded_nested.eml": build_forwarded_nested,
    "substack_style.eml": build_substack_style,
}


def main(argv: list[str]) -> int:
    force = "--force" in argv
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, builder in FIXTURE_BUILDERS.items():
        path = FIXTURES / name
        if path.exists() and not force:
            print(f"skip {name} (exists)")
            continue
        path.write_bytes(builder())
        print(f"wrote {name} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
