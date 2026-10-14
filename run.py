"""Entrypoint: serve the app with waitress (or the Flask dev server)."""

import waitress

from inbox2pod.api import app
from inbox2pod.config import Config

if __name__ == "__main__":
    if (Config.FLASK_ENV or "").lower().startswith("dev"):
        app.run(debug=True, host="0.0.0.0", port=Config.PORT)
    else:
        waitress.serve(app, host="0.0.0.0", port=Config.PORT)
