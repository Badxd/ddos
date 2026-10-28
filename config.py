# config.py — central config. Reads env, safe defaults.
import os

# ── Telegram ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID    = os.environ.get("API_ID", "")
API_HASH  = os.environ.get("API_HASH", "")

# ── Mongo (persistence) ───────────────────────────────────────────────────
MONGO_URI   = os.environ.get("MONGO_URI", "")
MONGO_DB    = os.environ.get("MONGO_DB", "ddosbot")
MONGO_USERS = "users"
MONGO_LOGS  = "command_logs"

# ── Admin ─────────────────────────────────────────────────────────────────
ADMIN_IDS = ["8789271943"]

# ── Attack policy ─────────────────────────────────────────────────────────
COOLDOWN_TIME    = 0      # seconds between attacks per non-admin user
DEFAULT_THREADS  = 100
MAX_THREADS      = 1000
MAX_DURATION     = 1000   # seconds
DEFAULT_DURATION = 60     # seconds if /attack <host:port> only
PAYLOAD_SIZE     = 1024   # UDP payload bytes

VECTORS = ("udp", "tcp", "http")
DEFAULT_VECTOR = "udp"

# ── Local file fallback (used only if MONGO_URI is empty) ─────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
USER_FILE = os.path.join(BASE_DIR, "users.txt")
LOG_FILE  = os.path.join(BASE_DIR, "log.txt")
