"""Central env-var configuration. Imported by every entrypoint."""
import os


def _get(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise RuntimeError(f"Missing required env var: {name}")
    return val


TELEGRAM_TOKEN = _get("TELEGRAM_TOKEN", required=True)
DATABASE_URL = _get("DATABASE_URL", required=True)

# Where the logged-in browser session is persisted (Northflank volume).
SESSION_PATH = _get("SESSION_PATH", "/data/storage_state.json")

# Where health-check / "session dead" alerts are DM'd. Optional but recommended.
_admin = _get("ADMIN_CHAT_ID", "")
ADMIN_CHAT_ID = int(_admin) if _admin.strip() else None

# Which store to automate: "blinkit" | "zepto".
PLATFORM = _get("PLATFORM", "blinkit").strip().lower()

# Comma-separated Telegram chat ids allowed to drive the bot.
# Empty => allow everyone (dev only). Use the `chatid` command to discover yours.
_allowed = _get("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS = {int(x.strip()) for x in _allowed.split(",") if x.strip()}

# Health endpoint Northflank can probe (long-poll needs no public ingress).
HEALTH_PORT = int(_get("HEALTH_PORT", "8080"))

# Run the browser headless in the container; set HEADLESS=false locally to watch.
HEADLESS = _get("HEADLESS", "true").strip().lower() != "false"

# Randomised human-paced delays (seconds) between browser actions.
MIN_DELAY = float(_get("MIN_DELAY", "0.8"))
MAX_DELAY = float(_get("MAX_DELAY", "2.2"))
