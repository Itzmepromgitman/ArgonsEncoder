<div align="center">
  <img src="https://i.ibb.co/RGJnsfC6/monkey-d-luffy-red-3840x2160-24473.png" alt="Argons Encoder Banner" width="100%" style="border-radius: 10px;">

  # 🎬 Argons Encoder

  **The Ultimate Telegram Video Encoding Bot**

  [![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
  [![Pyrogram](https://img.shields.io/badge/Pyrofork-v2.0-yellow?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.pyrogram.org/)
  [![FFmpeg](https://img.shields.io/badge/FFmpeg-Encoding-green?style=for-the-badge&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
  [![License](https://img.shields.io/badge/License-GPL--3.0-red?style=for-the-badge)](LICENSE)

  <p align="center">
    <a href="#-key-features">Features</a> •
    <a href="#-installation">Installation</a> •
    <a href="#-commands">Commands</a> •
    <a href="#-project-structure">Structure</a>
  </p>
</div>

---

## 🚀 Key Features

### 🎥 **Professional Encoding**
- **Codec Choice**: libx264 / libx265 / VP9 / AV1 (mpeg4 supported with automatic flag mapping).
- **No Wasted Pixels**: Never upscales — small sources keep their size.
- **Audio Control**: Codec (AAC/AC3/copy), bitrate, track selection or strip audio entirely.
- **Subtitle Control**: Copy or drop subtitle streams.
- **Sample Encodes**: Test settings on the first N seconds before a full run.
- **Trim / Clip**: Encode only a start→end window.
- **Remux Mode**: Container fixes via stream copy in seconds, zero quality loss.
- **Rename on Upload**: Output name patterns like `{original} [{res}]`.
- **Streamable Output**: Choose video (instant playback) or document delivery.
- **Auto Thumbnails**: Real frame previews on uploads when no custom thumb is set.

### ⚡ **Intelligent Queue System**
- **Concurrent Encoding**: Tunable worker slots (`MAX_CONCURRENT_JOBS`).
- **Persistence**: Queue state is debounced-persisted and restored after restarts.
- **Pause / Resume / Cancel**: Right from the progress card — with proper process control.
- **Per-user Limits**: Duplicate-job guard + configurable job cap per user.

### 🎨 **Premium User Experience**
- **Compact Progress Cards**: Bar, size, ETA, speed, FPS — no server-noise.
- **Error Details**: Friendly failure messages with a "Details" button (technical tail on tap).
- **Unified Queue View**: Numbered jobs with per-job cancel + refresh buttons.
- **Live /status**: Jobs, queue depth, CPU/RAM/disk, uptime.
- **Real /stats**: Total encodes, bytes processed, space saved.

### 🛠️ **Robust Management**
- **Maintenance Mode**: Reject new jobs while finishing current ones (`/maint`).
- **Ban / Unban**: Enforced at intake and on core user commands (`/ban`, `/unban`).
- **Owner /jobs**: Fleet-wide dashboard with one-tap cancel.
- **Startup Cleanup**: Transient dirs wiped on boot; logs rotated under `logs/`.
- **Health Endpoint**: aiohttp keep-alive server bound to `$PORT`.

### 🛡️ **Admin Features**
- **Broadcast**: Normal or pinned, with live progress and blocked-user cleanup.
- **Admin Panel**: Interactive owner GUI for admin management.
- **Owner-only Ops**: `/shell`, `/restart`, `/log` are hard-gated.

---

## 🛠️ Installation

### Prerequisites
- **Python 3.10+**
- **FFmpeg** (installed and in PATH)
- **MongoDB** (Database)
- **Telegram Bot Token** & **API Keys**

### 💻 Local Setup

1. **Clone the Repository**
   ```bash
   git clone https://github.com/Itzmepromgitman/ArgonsEncoder.git
   cd ArgonsEncoder
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**
   Create a `.env` file in the root directory:
   ```env
   TG_BOT_TOKEN=your_bot_token
   APP_ID=your_app_id
   API_HASH=your_api_hash
   OWNER_ID=your_telegram_id
   CHANNEL_ID=-100xxxxxxxx  # Your Log Channel ID
   DATABASE_URL=your_mongodb_uri
   DATABASE_NAME=Cluster0
   TG_BOT_WORKERS=8
   # Optional tuning
   MAX_CONCURRENT_JOBS=4
   MAX_CONCURRENT_UPLOADS=2
   MAX_JOBS_PER_USER=5
   UPDATE_ON_START=0
   ```

4. **Run the Bot**
   ```bash
   bash start.sh
   ```

### 🐳 Docker Deployment

```bash
# 1. Build Image
docker build -t argonsencoder .

# 2. Run Container
docker run -d --env-file .env --name encoder_bot argonsencoder
```

---

## 🤖 Commands

| Command | Description | Permission |
| :--- | :--- | :--- |
| `/start` | Initialize the bot & register user. | Everyone |
| `/settings` | Configure video settings (Codec, CRF, Audio, Trim…). | Everyone |
| `/queue` | View your jobs (owner sees all). | Everyone |
| `/status` | Live jobs + server load dashboard. | Everyone |
| `/stats` | Real encode statistics. | Everyone |
| `/ss` | Generate screenshots from video. | Everyone |
| `/cancel <id>` | Cancel a specific job. | Owner/User |
| `/clear` | Clear your queued jobs. | Everyone |
| `/help` | Usage manual. | Everyone |
| `/cancelall` | Cancel **ALL** jobs (with confirmation). | Owner Only |
| `/info <id>` | Detailed job info. | Owner Only |
| `/jobs` | Fleet-wide job dashboard. | Owner Only |
| `/restart` | Restart the bot server. | Owner Only |
| `/log` | Retrieve the bot's log file. | Owner Only |
| `/shell` | Execute Python code. | Owner Only |
| `/broadcast` | Broadcast message to users. | Admin Only |
| `/ban` `/unban` | Manage banned users. | Admin Only |
| `/maint` | Toggle maintenance mode. | Admin Only |
| `/admin` | Open Admin Panel. | Owner Only |

---

## 📁 Project Structure

```
ArgonsEncoder/
├── bot/
│   ├── func/           # Core Logic
│   │   ├── pyroutils/  # Progress Bar Utils
│   │   ├── encode.py   # Main Encoding Engine
│   │   ├── queue_manager.py
│   │   ├── download_manager.py
│   │   ├── upload_manager.py
│   │   ├── ffmpeg_utils.py
│   │   ├── editquery.py
│   │   └── preview.py
│   ├── utils/          # Helpers (format, restart, shell)
│   ├── config.py       # Config Loader (all tunables)
│   ├── logger.py       # Rotating file + Telegram error logs
│   ├── server.py       # aiohttp health endpoint
│   └── __main__.py     # Entry Point
├── plugins/            # Handlers
│   ├── admin.py
│   ├── encode.py
│   ├── query.py
│   ├── queue.py
│   ├── screenshot.py
│   ├── settings.py
│   └── start.py
├── tests/              # pytest suite
├── Dockerfile
├── requirements.txt
└── start.sh
```

---

## 📜 License

This project is licensed under the **GNU GPL v3.0** — see [LICENSE](LICENSE).

<div align="center">
  <br>
  <i>Built with ❤️ by <a href="https://t.me/ReactiveArgon"><b>Argon</b></a></i>
</div>
