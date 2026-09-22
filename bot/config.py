# Developed by ARGON telegram: @REACTIVEARGON
import os

from dotenv import load_dotenv

load_dotenv()


# --- Core identity ---
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
APP_ID = int(os.environ.get("APP_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
LOG_CHANNEL = int(os.environ.get("CHANNEL_ID", "-1001234567890"))
OWNER = os.environ.get("OWNER", "YOUR_OWNER_USERNAME_OR_ID")
OWNER_ID = int(os.environ.get("OWNER_ID", "1234567890"))
PORT = os.environ.get("PORT", "8030")
BOT_NAME = os.environ.get("BOT_NAME", "Argons Encoder")

# --- Database ---
DB_URI = os.environ.get(
    "DATABASE_URL",
    "mongodb+srv://user:password@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
)
DB_NAME = os.environ.get("DATABASE_NAME", "Cluster")
SESSION_DB_KEY = "bot_session_string"  # fixed key; never key secrets by raw token

TG_BOT_WORKERS = int(os.environ.get("TG_BOT_WORKERS", "50"))
MAX_CONCURRENT_TRANSMISSIONS = int(os.environ.get("MAX_CONCURRENT_TRANSMISSIONS", "8"))

# --- Concurrency limits ---
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "4"))
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "4"))
MAX_CONCURRENT_UPLOADS = int(os.environ.get("MAX_CONCURRENT_UPLOADS", "2"))
MAX_JOBS_PER_USER = int(os.environ.get("MAX_JOBS_PER_USER", "5"))

# --- Limits ---
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))

# --- Paths ---
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")
WATERMARK_DIR = os.environ.get("WATERMARK_DIR", "watermarks")
THUMB_DIR = os.environ.get("THUMB_DIR", "thumbs")
FONT_PATH = os.environ.get("FONT_PATH", os.path.join("bot", "fonts", "Roboto-Regular.ttf"))
LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_FILE_NAME = os.path.join(LOG_DIR, "bot.log")

# --- Timing / UI ---
UI_UPDATE_INTERVAL = float(os.environ.get("UI_UPDATE_INTERVAL", "3.0"))
PROGRESS_CALLBACK_INTERVAL = float(os.environ.get("PROGRESS_CALLBACK_INTERVAL", "3.0"))
FFMPEG_THREADS = max(1, int(os.environ.get("FFMPEG_THREADS", str(os.cpu_count() or 1))))

# --- Feature flags ---
UPDATE_ON_START = os.environ.get("UPDATE_ON_START", "0") == "1"


def is_placeholder_db_uri(uri: str) -> bool:
    return "user:password@" in uri or "cluster0.abcde" in uri
