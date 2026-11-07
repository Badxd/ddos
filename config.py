
import os
from pathlib import Path
from dotenv import load_dotenv

# Project directory
BASE_DIR = Path(__file__).resolve().parent

# Load .env from project directory
load_dotenv(BASE_DIR / ".env", override=False)

# Telegram Bot
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Telegram Userbot
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

# MongoDB
MONGO_URI = os.getenv("MONGO_URI", "")
MONGO_DB = os.getenv("MONGO_DB", "ddosbot")
MONGO_USERS = "users"
MONGO_LOGS = "command_logs"

# Admin
ADMIN_IDS = [
    admin.strip()
    for admin in os.getenv("ADMIN_IDS", "8789271943").split(",")
    if admin.strip()
]

# Attack configs
COOLDOWN_TIME = 0
DEFAULT_THREADS = 100
MAX_THREADS = 1000
MAX_DURATION = 1000
DEFAULT_DURATION = 60
PAYLOAD_SIZE = 1024


# File paths
USER_FILE = BASE_DIR / "users.txt"
LOG_FILE = BASE_DIR / "log.txt"
