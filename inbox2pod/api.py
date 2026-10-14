"""
Flask app: episodes UI, admin API and the podcast feed.
"""

import hmac
import math
import os

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, abort, g, render_template, request, send_file, send_from_directory
from flask_restx import Api, Resource, fields

from inbox2pod.config import Config
from inbox2pod.episodes import EpisodeOps
from inbox2pod.feed import gen_feed
from inbox2pod.ingest.smtp_source import SmtpReceiver
from inbox2pod.pipeline import (
    delete_mp3,
    main_poll_loop,
    re_extract,
    reconcile_tts_state_on_boot,
    tts_service,
)

DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500
TEXT_PREVIEW_LENGTH = 200

# Reachable without a feed token: no user data behind them
OPEN_PATHS = {"/health", "/favicon.ico", "/cover.png", "/feed.svg"}

episode_ops = EpisodeOps()

# In Flask dev mode the auto-reloader imports this module twice (parent watcher
# + child). Only start the workers in the child, or when not running under the
# reloader at all (e.g. waitress in prod).
_flask_env = (Config.FLASK_ENV or "").lower()
_in_reloader_parent = _flask_env.startswith("dev") and os.environ.get("WERKZEUG_RUN_MAIN") != "true"

smtp_receiver = None
if not _in_reloader_parent:
    Config.validate()
    reconcile_tts_state_on_boot()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        main_poll_loop,
        "interval",
        minutes=Config.IMAP_POLL_MINUTES,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=None,
    )
    scheduler.start()

    if Config.SMTP_ENABLED:
        smtp_receiver = SmtpReceiver()
        smtp_receiver.start()
else:
    scheduler = None

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Feed token gating
# ---------------------------------------------------------------------------

def _supplied_token() -> str | None:
    """The token from the query string or a header."""
    token = request.args.get("token")
    if token:
        return token.strip()

    header = request.headers.get("X-Feed-Token", "").strip()
    if header:
        return header

    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _path_token() -> str | None:
    """The token when it arrives as a path prefix: /<token>/feed.xml."""
    parts = [part for part in request.path.split("/") if part]
    return parts[0] if len(parts) > 1 else None


@app.before_request
def require_feed_token():
    """Podcast apps cannot do real auth, so a secret in the URL is the gate."""
    if not Config.FEED_TOKEN or request.path in OPEN_PATHS:
        return None

    supplied = _supplied_token() or _path_token()
    if supplied and hmac.compare_digest(supplied, Config.FEED_TOKEN):
        g.feed_token = supplied
        return None

    abort(401)


@app.context_processor
def token_template_vars():
    """Lets the UI carry the token through links and fetch calls."""
    token = getattr(g, "feed_token", "")

    def tokenized(path: str) -> str:
        if not token:
            return path
        separator = "&" if "?" in path else "?"
        return f"{path}{separator}token={token}"

    return {"feed_token": token, "tokenized": tokenized, "feed_title": Config.FEED_TITLE}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.route("/")
def show_episodes():
    """Episode list with extraction and TTS status."""
    per_page = min(max(request.args.get("per_page", DEFAULT_PER_PAGE, type=int), 1), MAX_PER_PAGE)
    page = max(request.args.get("page", 1, type=int), 1)
    episodes, total = episode_ops.get_episodes_page(page, per_page)
    page_count = max(math.ceil(total / per_page), 1)
    if page > page_count:
        page = page_count
        episodes, total = episode_ops.get_episodes_page(page, per_page)

    return render_template(
        "episodes.html",
        episodes=episodes,
        page=page,
        page_count=page_count,
        per_page=per_page,
        total=total,
        imap_enabled=Config.imap_enabled(),
        smtp_enabled=Config.SMTP_ENABLED,
    )


@app.route("/episode/<episode_id>")
def show_episode(episode_id):
    """Extraction debug view: what we pulled out of the raw email."""
    episode = episode_ops.get_episode(episode_id)
    if episode is None:
        abort(404)

    raw_exists = bool(episode.raw_path) and os.path.exists(episode.raw_path)
    return render_template(
        "episode.html",
        episode=episode,
        raw_exists=raw_exists,
        word_count=len((episode.text or "").split()),
        preview=(episode.text or "")[:TEXT_PREVIEW_LENGTH],
    )


@app.route("/health")
def health():
    """Liveness probe, plus whether the TTS backend is currently reachable."""
    return {
        "status": "healthy",
        "episodes": len(episode_ops.get_episode_ids()),
        "imap_enabled": Config.imap_enabled(),
        "smtp_enabled": Config.SMTP_ENABLED,
        "smtp_running": smtp_receiver is not None and smtp_receiver.running,
        "tts_healthy": tts_service.check_health(),
    }


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------


def render_feed() -> Response:
    """The RSS feed, newest FEED_MAX_EPISODES episodes only."""
    episodes = episode_ops.get_episodes_with_mp3()
    if len(episodes) > Config.FEED_MAX_EPISODES:
        episodes = episodes[-Config.FEED_MAX_EPISODES :]
    return Response(gen_feed(episodes, request.url_root), mimetype="application/rss+xml")


@app.route("/feed")
@app.route("/feed.xml")
@app.route("/feed.rss")
@app.route("/rss")
@app.route("/atom")
def feed():
    return render_feed()


@app.route("/<token_prefix>/feed")
@app.route("/<token_prefix>/feed.xml")
@app.route("/<token_prefix>/feed.rss")
@app.route("/<token_prefix>/rss")
@app.route("/<token_prefix>/atom")
def feed_prefixed(token_prefix):
    return render_feed()


@app.route("/cover.png")
def cover():
    return send_file(os.path.join(app.root_path, "cover.png"), mimetype="image/png")


@app.route("/feed.svg")
def feed_img():
    return send_file(os.path.join(app.root_path, "feed.svg"), mimetype="image/svg+xml")


@app.route("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(tts_service.mp3_storage_path, filename)


@app.route("/<token_prefix>/audio/<path:filename>")
def serve_audio_prefixed(token_prefix, filename):
    return send_from_directory(tts_service.mp3_storage_path, filename)


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

api = Api(
    app,
    version="1.0",
    title="inbox2pod API",
    description="Email newsletters turned into podcast episodes",
    doc="/docs",
)

ns = api.namespace("episodes", path="/episodes", description="Episode operations")

episode_model = api.model(
    "Episode",
    {
        "id": fields.String(required=True, description="Episode id"),
        "feed": fields.String(description="Feed slug"),
        "title": fields.String(required=True, description="Subject line, cleaned"),
        "description": fields.String(description="Feed description"),
        "text": fields.String(description="Extracted speakable text"),
        "sender_name": fields.String(description="From display name"),
        "sender_email": fields.String(description="From address"),
        "recipient": fields.String(description="To address"),
        "published_at": fields.DateTime(description="Email Date header, UTC"),
        "received_at": fields.DateTime(description="Ingest time, UTC"),
        "raw_path": fields.String(description="Stored .eml path"),
        "extractor": fields.String(description="Which extractor produced the text"),
        "extraction_ok": fields.Boolean(description="False when extraction was too thin to synthesize"),
        "tts_job_id": fields.String(description="TTS job id, null when pending"),
        "mp3": fields.String(description="Synthesized audio file"),
    },
)


def _delete_episode_files(episode) -> None:
    delete_mp3(episode.mp3)
    if episode.raw_path and os.path.exists(episode.raw_path):
        os.remove(episode.raw_path)


@ns.route("/")
class EpisodeList(Resource):
    @ns.doc("list_episodes")
    @ns.marshal_list_with(episode_model)
    def get(self):
        """List all episodes"""
        return episode_ops.get_all_episodes(sort_by_published_at=True)


@ns.route("/tts_waiting")
class TTSWaiting(Resource):
    @ns.doc("list_episodes_waiting_for_tts")
    @ns.marshal_list_with(episode_model)
    def get(self):
        """Episodes waiting for TTS"""
        return episode_ops.get_episodes_to_tts()


@ns.route("/force_update")
class ForceUpdate(Resource):
    @ns.doc("force_update")
    def get(self):
        """Poll for new mail and run a TTS cycle now"""
        main_poll_loop()
        return "OK"

    def post(self):
        """Poll for new mail and run a TTS cycle now"""
        return self.get()


@ns.route("/<episode_id>")
class Episode(Resource):
    @ns.doc("delete_episode")
    def delete(self, episode_id):
        """Delete an episode, its audio and its raw email"""
        episode = episode_ops.get_episode(episode_id)
        if episode is None:
            return "Episode not found", 404

        print("Deleting episode", episode_id)
        _delete_episode_files(episode)
        episode_ops.delete_episode(episode_id)
        return "OK"


@ns.route("/tts/<episode_id>")
class EpisodeTTS(Resource):
    @ns.doc("request_new_tts")
    def delete(self, episode_id):
        """Re-synthesize an episode"""
        if episode_ops.get_episode(episode_id) is None:
            return "Episode not found", 404

        print("Requesting new TTS run for episode", episode_id)
        delete_mp3(episode_ops.get_episode_mp3(episode_id))
        episode_ops.clear_tts(episode_id)
        main_poll_loop()
        return "OK"


@ns.route("/re_extract/<episode_id>")
class EpisodeReExtract(Resource):
    @ns.doc("re_extract_episode")
    def post(self, episode_id):
        """Re-run extraction from the stored raw email and queue a fresh TTS run"""
        if episode_ops.get_episode(episode_id) is None:
            return "Episode not found", 404

        extraction = re_extract(episode_id)
        if extraction is None:
            return "No raw email stored for this episode", 409

        return {
            "extractor": extraction.extractor,
            "extraction_ok": extraction.ok,
            "chars": len(extraction.text),
        }


@ns.route("/feed")
class Feed(Resource):
    @ns.doc("get_feed")
    def get(self):
        """Get the feed"""
        return render_feed()


if __name__ == "__main__":
    port = Config.PORT
    if _flask_env.startswith("dev"):
        app.run(debug=True, host="0.0.0.0", port=port)
    else:
        import waitress

        waitress.serve(app, host="0.0.0.0", port=port)
