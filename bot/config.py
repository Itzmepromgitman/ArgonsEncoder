# Developed by ARGON telegram: @REACTIVEARGON
import os
import shutil

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
BOT_VERSION = os.environ.get("BOT_VERSION", "2.3.0")

# --- Database ---
DB_URI = os.environ.get(
    "DATABASE_URL",
    "mongodb+srv://user:password@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
)
DB_NAME = os.environ.get("DATABASE_NAME", "Cluster")
TG_BOT_WORKERS = max(1, int(os.environ.get("TG_BOT_WORKERS", "8")))
MAX_CONCURRENT_TRANSMISSIONS = max(
    1, int(os.environ.get("MAX_CONCURRENT_TRANSMISSIONS", "8"))
)

# --- Concurrency limits ---
MAX_CONCURRENT_JOBS = max(1, int(os.environ.get("MAX_CONCURRENT_JOBS", "4")))
MAX_CONCURRENT_DOWNLOADS = max(
    1, int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "4"))
)
MAX_CONCURRENT_UPLOADS = max(1, int(os.environ.get("MAX_CONCURRENT_UPLOADS", "2")))
MAX_JOBS_PER_USER = max(1, int(os.environ.get("MAX_JOBS_PER_USER", "5")))
MAX_QUEUE_LENGTH = max(10, int(os.environ.get("MAX_QUEUE_LENGTH", "500")))

# --- Limits ---
MAX_FILE_SIZE = max(
    1, int(os.environ.get("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))
)
MAX_MEDIA_DURATION = max(
    60, int(os.environ.get("MAX_MEDIA_DURATION", "21600"))
)
MIN_FREE_DISK_BYTES = max(
    0, int(os.environ.get("MIN_FREE_DISK_BYTES", str(2 * 1024 * 1024 * 1024)))
)
MAX_OUTPUT_SIZE = max(
    1, int(os.environ.get("MAX_OUTPUT_SIZE", str(4 * 1024 * 1024 * 1024)))
)
UPLOAD_RETRY_CAP = max(
    20, int(os.environ.get("UPLOAD_RETRY_CAP", "1000"))
)
MAX_OUTPUT_VARIANTS = max(
    1, int(os.environ.get("MAX_OUTPUT_VARIANTS", "4"))
)
UPLOAD_RETRY_PER_USER = max(
    1, int(os.environ.get("UPLOAD_RETRY_PER_USER", "3"))
)
MAX_RECOVERY_DISK_BYTES = max(
    1, int(os.environ.get("MAX_RECOVERY_DISK_BYTES", str(20 * 1024 * 1024 * 1024)))
)
MIN_OUTPUT_RESERVATION_BYTES = max(
    64 * 1024 * 1024,
    int(os.environ.get("MIN_OUTPUT_RESERVATION_BYTES", str(256 * 1024 * 1024))),
)

# --- Paths ---
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")
WATERMARK_DIR = os.environ.get("WATERMARK_DIR", "watermarks")
THUMB_DIR = os.environ.get("THUMB_DIR", "thumbs")
FONT_PATH = os.environ.get("FONT_PATH", os.path.join("bot", "fonts", "Roboto-Regular.ttf"))
LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_FILE_NAME = os.path.join(LOG_DIR, "bot.log")

# --- Timing / UI ---
UI_UPDATE_INTERVAL = max(0.5, float(os.environ.get("UI_UPDATE_INTERVAL", "3.0")))
PROGRESS_CALLBACK_INTERVAL = max(
    0.5, float(os.environ.get("PROGRESS_CALLBACK_INTERVAL", "3.0"))
)
# Divide CPU budget across concurrent encoders by default. Letting every FFmpeg
# process use every core causes severe contention when the queue is busy.
_DEFAULT_FFMPEG_THREADS = max(1, (os.cpu_count() or 1) // MAX_CONCURRENT_JOBS)
FFMPEG_THREADS = min(
    16,
    max(
        1,
        int(os.environ.get("FFMPEG_THREADS", str(_DEFAULT_FFMPEG_THREADS))),
    ),
)
FFMPEG_WALL_TIMEOUT = max(
    60, int(os.environ.get("FFMPEG_WALL_TIMEOUT", "28800"))
)
PAUSED_JOB_TTL = max(300, int(os.environ.get("PAUSED_JOB_TTL", "7200")))

# --- Feature flags ---
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")
# Delivery logging is opt-in because encoded media may contain private data.
LOG_DELIVERIES = os.environ.get("LOG_DELIVERIES", "0") == "1"
ERROR_LOGS_TO_TELEGRAM = os.environ.get("ERROR_LOGS_TO_TELEGRAM", "0") == "1"
UPDATE_ON_START = os.environ.get("UPDATE_ON_START", "0") == "1"


def is_placeholder_db_uri(uri: str) -> bool:
    return "user:password@" in uri or "cluster0.abcde" in uri


def validate_config() -> None:
    """Fail fast with actionable configuration errors before clients start."""
    errors = []
    if TG_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or not TG_BOT_TOKEN.strip():
        errors.append("TG_BOT_TOKEN is missing or still uses the placeholder")
    if APP_ID <= 0:
        errors.append("APP_ID must be a positive integer")
    if API_HASH == "your_api_hash_here" or not API_HASH.strip():
        errors.append("API_HASH is missing or still uses the placeholder")
    if OWNER_ID <= 0 or OWNER_ID == 1234567890:
        errors.append("OWNER_ID must identify the bot owner")
    if LOG_CHANNEL >= 0:
        errors.append("CHANNEL_ID must be a negative supergroup/channel ID")
    if is_placeholder_db_uri(DB_URI):
        errors.append("DATABASE_URL still uses the placeholder MongoDB URI")
    if not (1 <= int(PORT) <= 65535):
        errors.append("PORT must be between 1 and 65535")
    if min(MAX_CONCURRENT_JOBS, MAX_CONCURRENT_DOWNLOADS, MAX_CONCURRENT_UPLOADS) < 1:
        errors.append("concurrency limits must be at least 1")
    if (
        MAX_FILE_SIZE < 1
        or MAX_JOBS_PER_USER < 1
        or MAX_MEDIA_DURATION < 60
        or MAX_OUTPUT_SIZE < 1
        or MAX_QUEUE_LENGTH < 10
        or FFMPEG_WALL_TIMEOUT < 60
        or UPLOAD_RETRY_CAP < 20
        or MAX_OUTPUT_VARIANTS < 1
        or UPLOAD_RETRY_PER_USER < 1
        or MIN_OUTPUT_RESERVATION_BYTES < 64 * 1024 * 1024
    ):
        errors.append("file, duration, output, queue, and runtime limits are invalid")
    if not shutil.which(FFMPEG_BIN):
        errors.append(f"FFMPEG_BIN is not executable: {FFMPEG_BIN}")
    if not shutil.which(FFPROBE_BIN):
        errors.append(f"FFPROBE_BIN is not executable: {FFPROBE_BIN}")
    if errors:
        raise RuntimeError("Invalid configuration:\n- " + "\n- ".join(errors))
