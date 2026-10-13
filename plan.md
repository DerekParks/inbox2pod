# inbox2pod — Email newsletters → self-hosted podcast feed

Sibling project to [HoarderToPod](https://github.com/DerekParks/HoarderToPod) and
[FlaskTtS](https://github.com/DerekParks/FlaskTtS). Newsletters arrive by email,
get converted to clean speakable text, synthesized via FlaskTtS, and served as a
private podcast RSS feed.

**Why this exists:** HoarderToPod requires running karakeep + a plugin. Almost
nobody does that. But *everybody* drowns in email newsletters. Same pipeline,
much bigger front door.

---

## Architecture verdict (read this first)

Derek's sketch was `SMTP client -> scrape text -> FlaskTTS -> podcast xml`.
That's directionally right with three corrections:

1. **It's an SMTP *server* (receiver), not a client** — and it should NOT be the
   only way in. Ship **two pluggable ingest sources behind one interface**:
   - **IMAP poller (the default)** — points at any existing mailbox
     (Gmail/Fastmail/whatever) and pulls from a folder/label. Zero DNS, zero
     open ports, works behind CGNAT, works for the 99% of potential users who
     will never set up an MX record. Users either auto-forward newsletters to a
     dedicated address or just apply a label/filter.
   - **SMTP receiver (the power mode)** — embedded `aiosmtpd` listener. With a
     domain + MX record you subscribe newsletters *directly* to
     `something@pod.example.com`. Cooler, fully self-contained, no third-party
     mailbox. Requires a VPS or open port 25, so it's opt-in.

   Both produce the same `IncomingEmail` object and share everything downstream.
   Both are small (~150 lines each). This is the difference between "neat hack
   for Derek" and "useful to others."

2. **There is no scraping.** The content arrives *in* the email. This is MIME
   parsing + HTML boilerplate removal, which is easier and more reliable than
   web scraping. (A "follow the web-version link for truncated newsletters"
   feature can come later — see Future work.)

3. **Reuse the HoarderToPod backend loop wholesale.** `tts_service.py`
   (including WoL wake), the Episode/SQLAlchemy pattern, the
   APScheduler-in-Flask worker, `gen_feed`, and the episodes UI are all proven
   by a year of daily use. Copy them in and genericize; do NOT extract a shared
   library yet (premature — do it if a third *ToPod ever appears).

### Verified external interface: FlaskTtS async job API

Confirmed against current FlaskTtS main (2026-07):

- `POST /tts/synthesize` `{text, model: style2tts|kokoro|qwen3, voice?}` → `202 {job_id}`
- `GET /tts/jobs` → `{jobs: [{job_id, status: PENDING|RUNNING|COMPLETED|FAILED}]}`
- `GET /tts/jobs/{id}/download` → mp3 bytes
- `DELETE /tts/jobs/{id}`
- `GET /health/check`

Long-text chunking is handled inside FlaskTtS (qwen3 chunking landed recently) —
inbox2pod submits whole episode text in one job. HoarderToPod's
`tts_service.py` client matches this surface exactly; lift it unchanged except
imports/config.

---

## System shape

One container, one process (Flask + background threads), same as HoarderToPod:

```
                       ┌──────────────────────────── inbox2pod ────────────────────────────┐
  newsletter ──SMTP──► │ aiosmtpd listener ─┐                                              │
                       │                    ├─► dedupe ─► store raw .eml ─► extract ─► DB  │
  mailbox ◄──IMAP───── │ IMAP poller ───────┘            (Message-ID)      (html→text)    │
                       │                                                                   │
                       │ TTS worker loop: submit pending ─► poll jobs ─► download mp3      │──► FlaskTtS
                       │                                                                   │
  podcast client ◄──── │ Flask: /feed.xml (token-gated)  /audio/*  episodes UI + API       │
                       └───────────────────────────────────────────────────────────────────┘
```

- **No Redis/Celery/queue.** SQLite *is* the queue (`tts_job_id IS NULL` = pending),
  exactly like HoarderToPod. Self-hosters get one container + one optional GPU box.
- **APScheduler** drives the IMAP poll and the TTS reconcile loop.
- **aiosmtpd** runs on a daemon thread with its own asyncio loop, enabled only
  when `SMTP_ENABLED=true`.
- SQLite for metadata, filesystem for `.eml` and `.mp3`.

### Package layout

```
inbox2pod/
  config.py          # env-driven, HoarderToPod style
  ingest/
    base.py          # IncomingEmail dataclass; source interface
    imap_source.py   # imap-tools poller
    smtp_source.py   # aiosmtpd handler
  extract.py         # MIME → clean speakable text (the real work, see below)
  episodes.py        # SQLAlchemy model + ops   [lifted from HoarderToPod]
  tts_service.py     # FlaskTtS client + WoL    [lifted verbatim]
  feed.py            # feedgen RSS w/ podcast ext [lifted from run.py:gen_feed]
  pipeline.py        # orchestration: ingest→extract→persist; tts submit/poll loop
  api.py             # Flask app, flask-restx, episodes UI
  run.py             # entrypoint (waitress in prod)
tests/
  fixtures/*.eml     # real newsletter corpus — the heart of the test suite
  test_extract.py
  test_ingest_*.py
  test_feed.py
Dockerfile
docker-compose.yaml  # inbox2pod + commented-out FlaskTtS section
sample.env
```

### Data model

```python
class Episode(Base):
    __tablename__ = "episodes"
    id            = Column(String, primary_key=True)   # sha256(Message-ID)
    feed          = Column(String, default="default", nullable=False)  # future multi-feed; index it
    title         = Column(String, nullable=False)     # Subject, cleaned
    description   = Column(Text)                       # preheader or first ~300 chars
    text          = Column(Text)                       # extracted speakable text
    sender_name   = Column(String)                     # From display name ("Money Stuff")
    sender_email  = Column(String)                     # From addr (allowlist checks, future routing)
    recipient     = Column(String)                     # To addr (future plus-address routing)
    published_at  = Column(DateTime, nullable=False)   # email Date header (UTC)
    received_at   = Column(DateTime, nullable=False)   # ingest time (UTC)
    raw_path      = Column(String, nullable=False)     # stored .eml
    tts_job_id    = Column(String)                     # NULL = pending TTS
    mp3           = Column(String)                     # NULL = not synthesized yet
```

Keep HoarderToPod's `EpisodeOps` semantics (`get_episodes_to_tts`,
`mark_tts_submitted`, `null_episodes_that_tts_doesnt_know_about`,
`clear_tts`, …) — the boot-time TTS reconcile logic is battle-tested, keep it.

**Always store the raw `.eml`** (`data/raw/{id}.eml`). Extraction WILL improve
over time; raw mail lets any episode be re-extracted + re-synthesized later
(`clear_tts` + re-run extract) instead of being lost. Disk cost is trivial.

### Dedupe

Primary key = sha256 of the `Message-ID` header (fall back to
sha256(From+Date+Subject) when absent). Makes IMAP re-polls, overlapping
IMAP+SMTP delivery, and newsletter re-sends all idempotent for free.

---

## Ingest details

### `ingest/base.py`

```python
@dataclass
class IncomingEmail:
    message_id: str
    subject: str
    sender_name: str
    sender_email: str
    recipient: str
    date: datetime          # UTC
    raw_bytes: bytes        # full RFC822 message
```

Each source yields `IncomingEmail`s into a single `process_incoming(email)`
function in `pipeline.py`. Sources never touch the DB directly.

### IMAP poller (`imap_source.py`) — default path

- Library: **imap-tools** (clean API over stdlib imaplib; handles IDLE later if wanted).
- Config: `IMAP_HOST/PORT/USER/PASSWORD/FOLDER` (default `INBOX`),
  `IMAP_POLL_MINUTES` (default 5).
- Fetch unseen (or all-newer-than-last-poll; dedupe makes either safe), yield, then
  per `IMAP_AFTER_ACTION`: `mark_seen` (default) | `move:<folder>` | `delete` | `none`.
- Auth note for docs: Gmail = app password; recommend a dedicated mailbox or a
  label + forwarding filter rather than pointing it at a primary inbox.

### SMTP receiver (`smtp_source.py`) — power path

- Library: **aiosmtpd**, `SMTP_ENABLED=false` by default, `SMTP_LISTEN_PORT=2525`
  in-container (compose maps `25:2525`). STARTTLS optional/later — newsletter
  senders will deliver without it; don't block v1 on cert plumbing.
- **Spam defense — required, port 25 is a firehose:**
  - `SMTP_ALLOWED_RCPT`: comma list of accepted recipient local-parts/addresses.
    Docs recommend a *random* local part (`x7kq2m@pod.example.com`) — a secret
    address is the auth. Reject everything else at RCPT time (550).
  - `SENDER_ALLOWLIST` (optional, applies to both sources): comma list of
    addresses/domains. When set, non-matching mail is stored with a
    `rejected` flag or dropped (start simple: log + drop; UI triage later).
  - Max message size (`SMTP_MAX_SIZE`, default 10MB).
- Handler does no heavy work in the SMTP transaction: parse envelope → write
  `.eml` → hand off to pipeline thread → return 250.

---

## Extraction (`extract.py`) — the actual hard part

Newsletter HTML is hostile: layout tables, tracking pixels, invisible
preheaders, sponsor blocks, "view in browser" / unsubscribe footers, emoji
soup, links every third word. This module decides whether the podcast is
listenable. **Front-load it and build it test-first against real fixtures.**

Pipeline:

1. **MIME walk** (stdlib `email`, `policy=default`): prefer `text/html` part,
   fall back to `text/plain`. Decode charsets defensively; run **ftfy** (already
   a HoarderToPod dep) over the result.
2. **HTML → text.** Evaluate on the fixture corpus, in this order of expected
   quality, and pick per-corpus (decide with data, not vibes):
   - **trafilatura** — best-in-class boilerplate removal; built for web pages
     but generally strong on newsletter HTML.
   - **readability-lxml + markdownify/html2text** — the HoarderToPod-ish path.
   - **html2text on pre-cleaned soup** (BeautifulSoup: drop `style/script`,
     hidden elements, width-1 images) — the dumb fallback that always produces
     *something*.
   Wire the winner as default with the dumb path as fallback when the winner
   returns empty/tiny output.
3. **Speakability pass** (regex/heuristic, unit-test each):
   - Strip URLs (speak link text only, never the href).
   - Cut footer from first strong marker: `unsubscribe`, `manage preferences`,
     `view in browser`, `you are receiving this`, © lines.
   - Strip preheader junk (zero-width chars, `&zwnj;`, hidden repeated chars).
   - Collapse whitespace; drop image-only content (alt text only when short).
   - Markdown-ish leftovers (`*`, `#`, `|` tables) → plain sentences.
4. Return `(text, description)` — description = first ~300 chars, for the feed.

Guardrail: if extracted text < ~200 chars, mark the episode failed-extraction
(visible in UI) rather than synthesizing a 4-second episode.

**Fixture corpus:** Derek exports 8–12 real newsletters as `.eml`
(Gmail: ⋮ → Show original → Download original). Mix long-form single-article
(Money Stuff style), digest (TLDR style), image-heavy (product/brand style).
Sanitize before commit (they're going in a public repo): scrub recipient
address + tracking tokens. Every extraction bug ever found becomes a fixture.

---

## Web layer (`api.py`)

Lift HoarderToPod's Flask + flask-restx + APScheduler-in-app structure,
including the werkzeug-reloader double-import guard.

- `GET /feed.xml` (+ aliases `/feed`, `/rss`) — feedgen RSS with podcast
  extension. Changes vs HoarderToPod's `gen_feed`: author = sender_name,
  link = project/self URL (episodes have no canonical web URL), keep
  `sanitize_xml_string` everywhere, `FEED_TITLE`/`FEED_DESCRIPTION` env-driven,
  new `cover.jpg`.
- **Feed auth:** if `FEED_TOKEN` is set, `/feed.xml` and `/audio/*` require
  `?token=` or a `/{token}/feed.xml` path prefix (podcast apps can't do real
  auth; secret-in-URL is the industry standard). Unset = LAN mode, open, same
  as HoarderToPod. This matters more here than in HoarderToPod because the SMTP
  mode implies an internet-facing box.
- Episodes UI: reuse `episodes.html` — list, delete, re-TTS. Add: extraction
  status, "view extracted text" (debug extraction quality from the browser),
  re-extract-from-raw button.
- API: list/delete/re-tts/re-extract/force-poll, mirroring HoarderToPod.

## Config (env, `sample.env` documents all)

```
# Ingest
IMAP_HOST / IMAP_PORT=993 / IMAP_USER / IMAP_PASSWORD / IMAP_FOLDER=INBOX
IMAP_POLL_MINUTES=5 / IMAP_AFTER_ACTION=mark_seen
SMTP_ENABLED=false / SMTP_LISTEN_PORT=2525 / SMTP_ALLOWED_RCPT / SMTP_MAX_SIZE=10485760
SENDER_ALLOWLIST=            # empty = accept all

# TTS (identical to HoarderToPod incl. WoL block)
TTS_ROOT_URL=http://localhost:5001 / TTS_MODEL=kokoro / TTS_VOICE=af_heart
TTS_WOL_ENABLED=false / TTS_WOL_MAC / ...

# Storage & feed
DATABASE_URI=sqlite:///data/inbox2pod.db
RAW_STORAGE_PATH=data/raw / MP3_STORAGE_PATH=data/audio
FEED_TITLE="Inbox2Pod" / FEED_DESCRIPTION / FEED_TOKEN= / FEED_MAX_EPISODES=1000
PORT=5003
```

At least one ingest source must be configured; assert at boot with a clear message.

---

## Milestones (each independently shippable)

### M0 — Scaffold + lift (small)
uv project, pyproject, ruff/pytest config. Copy in `tts_service.py` (verbatim),
`episodes.py` (new schema, keep ops semantics), `feed.py` (from `run.py:gen_feed`),
`utils.py` helpers, `config.py` skeleton. Port HoarderToPod's existing tests for
lifted code. Green pytest.

### M1 — Extraction core (the risky one — do it second, prove it early)
`.eml` fixture corpus (Derek supplies, sanitized) → `extract.py` per the design
above → extractor bake-off on fixtures → speakability heuristics with unit
tests each → `pipeline.process_incoming()` (dedupe → store raw → extract →
Episode row). **Checkpoint: CLI `python -m inbox2pod.extract fixture.eml`
prints text Derek would actually want read aloud.**

### M2 — IMAP ingest + TTS loop = end-to-end usable
`imap_source.py` + poll scheduling; port HoarderToPod's TTS submit/poll/
download/reconcile loop; minimal `/feed.xml` + `/audio/*`. **Checkpoint: forward
a real newsletter → episode plays in a podcast app. Derek starts daily-driving
here.**

### M3 — Web UI + feed token
Episodes UI w/ extraction debug views, admin API, `FEED_TOKEN` gating,
cover art.

### M4 — SMTP receiver
`smtp_source.py`, RCPT/sender/size guards, thread lifecycle, compose port
mapping. Test with `swaks`. Docs: MX setup, random-local-part pattern.

### M5 — Ship it
Dockerfile (uv-based, non-root), compose (+ commented FlaskTtS), sample.env,
README (the "why", both ingest modes with honest tradeoffs, Gmail app-password
+ filter walkthrough, MX walkthrough), GH Actions image build mirroring
HoarderToPod's cd.yaml, AI-disclosure section (house style).

### M6 — Future (explicitly NOT v1)
- **Multi-feed routing:** recipient plus-address or sender rules → `feed`
  column → `/feeds/{slug}.xml`. Schema is ready; don't build until wanted.
- **Truncated-newsletter fetch:** follow "read online" link, extract web
  version (newspaper4k/trafilatura — this IS scraping, and where
  `archive_scraper.py` patterns could return).
- **Optional LLM cleanup/summarization plugin** (core must never require it).
- **RSS-source ingest** (subsumes kill-the-newsletter users); IMAP IDLE;
  auto-expire old episodes + mp3s; itunes:duration via ffprobe.

## Testing strategy

- Extraction: fixture-driven, every real-world failure becomes a `.eml` fixture.
- Ingest: IMAP mocked via imap-tools fakes/greenmail-style fixtures; SMTP by
  driving aiosmtpd handler directly with raw messages (no network).
- Pipeline: tmp SQLite + tmp dirs; TTS service mocked at `requests` level
  (HoarderToPod's `test_tts_service.py` shows the pattern).
- Feed: golden-file RSS assertions + `feedgen` round-trip.
- Per global preferences: run full pytest after every change.

## Open questions for Derek (defaults chosen; override at will)

1. **Digest newsletters** (TLDR etc.): v1 reads the whole digest as one
   episode. Split-into-multiple-episodes is an M6 candidate — worth it?
2. Episodes from allowlist-rejected senders: log-and-drop (current plan) or
   quarantine table + UI triage?
3. `FEED_TOKEN` style: `?token=` query param or path prefix? (Plan: support
   both, path prefix documented as primary — some podcast apps strip query
   params.)
4. License MIT, name `inbox2pod` — assumed yes.
