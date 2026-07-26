"""Central env-var configuration. Imported by every entrypoint."""
import os


def _load_dotenv(path=".env"):
    """Minimal .env loader for local dev. No-op in prod (no file, real env vars).
    Never overrides variables already present in the environment."""
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()


def _get(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise RuntimeError(f"Missing required env var: {name}")
    return val


TELEGRAM_TOKEN = _get("TELEGRAM_TOKEN", required=True)
DATABASE_URL = _get("DATABASE_URL", required=True)

# Guest delivery location (pincode or locality). No login needed — Blinkit only
# requires a delivery location to browse, add to cart, and share. e.g. "411057".
BLINKIT_LOCATION = _get("BLINKIT_LOCATION", required=True)

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

# Optional proxy for the browser (e.g. an India IP, since Blinkit is India-only
# and datacenter IPs get no products). Format: "http://host:port" or
# "socks5://host:port". Leave empty to connect directly.
_proxy_server = _get("PROXY_SERVER", "")
PROXY = None
if _proxy_server.strip():
    PROXY = {"server": _proxy_server.strip()}
    _pu, _pp = _get("PROXY_USERNAME", ""), _get("PROXY_PASSWORD", "")
    if _pu.strip():
        PROXY["username"] = _pu.strip()
    if _pp.strip():
        PROXY["password"] = _pp.strip()
