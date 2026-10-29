import os

# Telegram Bot
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Telegram Userbot (String Session for VC Extraction)
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# Mongo DB
MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB", "ddosbot")
MONGO_USERS = "users"
MONGO_LOGS = "command_logs"

# Admin
ADMIN_IDS = ["8789271943"]

# Attack configs
COOLDOWN_TIME = 0
DEFAULT_THREADS = 100
MAX_THREADS = 1000
MAX_DURATION = 1000
DEFAULT_DURATION = 60
PAYLOAD_SIZE = 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_FILE = os.path.join(BASE_DIR, "users.txt")
LOG_FILE = os.path.join(BASE_DIR, "log.txt")
