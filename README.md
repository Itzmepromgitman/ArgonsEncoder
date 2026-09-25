<div align="center">
  <img src="https://i.ibb.co/RGJnsfC6/monkey-d-luffy-red-3840x2160-24473.png" alt="Argons Encoder Banner" width="100%" style="border-radius: 10px;">

  # 🎬 Argons Encoder

  **A private Telegram video encoding bot powered by Pyrofork/MTProto and FFmpeg**

  [![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
  [![Pyrofork](https://img.shields.io/badge/MTProto-Pyrofork-2AABEE?style=for-the-badge&logo=telegram&logoColor=white)](https://github.com/Mayuri-Chan/pyrofork)
  [![MongoDB](https://img.shields.io/badge/MongoDB-required-47A248?style=for-the-badge&logo=mongodb&logoColor=white)](https://www.mongodb.com/)
  [![FFmpeg](https://img.shields.io/badge/FFmpeg-Encoding-646C00?style=for-the-badge&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
  [![License](https://img.shields.io/badge/License-GPL--3.0-red?style=for-the-badge)](LICENSE)

  <p align="center">
    <a href="#features">Features</a> •
    <a href="#architecture">Architecture</a> •
    <a href="#setup">Setup</a> •
    <a href="#configuration">Configuration</a> •
    <a href="#commands">Commands</a> •
    <a href="#safety-and-privacy">Safety</a> •
    <a href="#reliability-recovery-and-operations">Reliability</a> •
    <a href="#testing-and-validation">Testing</a> •
    <a href="#deployment">Deployment</a>
  </p>
</div>

---

## Overview

Argons Encoder accepts a supported video in a **private Telegram chat**, downloads it, places the work in a persistent queue, probes the media with `ffprobe`, encodes or remuxes it with FFmpeg, and delivers the result back to the same user. Users can configure quality, audio, subtitles, branding, trim/sample windows, output format, and multiple resolutions from the bot UI.

This repository is a **Pyrofork/MTProto application**, not a Telegram Bot API HTTP service. The `aiohttp` component in this project is a small health/keep-alive server only; Telegram updates are handled by the Pyrofork `Client` and the `plugins/` handlers. Group-chat workflows, webhooks, Mini App-only controls, and other Bot API-only interfaces are outside the project scope.

> **Developer:** ARGON telegram — [@REACTIVEARGON](https://t.me/ReactiveArgon)

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Setup](#setup)
- [Configuration](#configuration)
- [Accepted media and limits](#accepted-media-and-limits)
- [Commands](#commands)
- [Safety and privacy](#safety-and-privacy)
- [Reliability, recovery, and operations](#reliability-recovery-and-operations)
- [Testing and validation](#testing-and-validation)
- [Deployment](#deployment)
- [Project structure](#project-structure)
- [License and credits](#license-and-credits)

## Features

### Video encoding

- **Quality profiles:** Fast, Balanced, and Compact presets change encoding quality while retaining trim, resolutions, branding, metadata, and delivery choices.
- **Codecs:** `libx264`, `libx265`, `libvpx-vp9`, `libaom-av1`, and `mpeg4`; codec-specific flags are selected by the command generator.
- **Multiple resolutions:** 1080p, 720p, 480p, and 360p can be selected together. Every generated variant is retained for delivery.
- **No upscaling:** the scale filter caps output height at the source height.
- **Audio control:** AAC, AC3, or stream copy; bounded bitrate selection; all tracks, one 1-based track, or no audio.
- **Subtitle control:** copy subtitle streams or drop them. Streamable MP4 delivery uses `mov_text` for text subtitles.
- **Sample encodes:** test a bounded number of seconds before committing to a full encode.
- **Trim and clip:** encode a `start → end` window using the configured duration limits.
- **Remux mode:** copy streams instead of re-encoding when a container change is enough. Scaling, watermarking, CRF, and other re-encode filters are not applied in this mode.
- **Output delivery:** send as a streamable video or as a document; if streamable video delivery is unavailable, the bot attempts document delivery.
- **Thumbnails:** normalize a custom thumbnail for Telegram, or generate a frame from the encoded output when no custom thumbnail is available.
- **Metadata and naming:** edit global/video/audio/subtitle metadata and use bounded `{original}`, `{res}`, `{codec}`, and `{date}` rename tokens.

### Queue and delivery

- **Bounded concurrency:** separate FFmpeg, download, and upload worker limits.
- **Persistent encoding queue:** queue state is saved to MongoDB with a debounced snapshot and restored after a process restart.
- **Pause, resume, and cancel:** controls are available on the progress card and queue view; cancellation terminates the FFmpeg process and its children.
- **Per-user limits:** duplicate-source protection and a configurable active-job cap.
- **Disk reservations:** new work is admitted only when the estimated source/output reservation and free-space floor can be satisfied.
- **Upload recovery:** a failed or interrupted delivery can remain as a local recovery entry with a user-owned retry action.

### User experience and management

- Progress cards show percentage, output size, ETA, elapsed time, speed, FPS, bitrate, codec, and job ID.
- User queue and status views are scoped to the requesting user; owner views expose fleet-wide encoding state.
- `/settings` provides a shared UI for video, audio, watermark, thumbnail, output, metadata, quality presets, and advanced overrides.
- Maintenance mode rejects new intake while already-running jobs continue.
- Ban/unban state, an admin list, broadcasts, an admin panel, and owner-only operations are available.
- Rotating file logs are written under `logs/`; optional Telegram error notifications are disabled by default.
- `/` and `/healthz` expose a small JSON health endpoint on `$PORT`.

## Architecture

The runtime is intentionally split into Telegram handlers, media/queue workers, persistence, and a small HTTP health endpoint:

1. **Telegram client:** `bot/__main__.py` creates a Pyrofork `Client` with `TG_BOT_TOKEN`, `APP_ID`, `API_HASH`, plugin loading, Pyrofork workers, and a transmission cap.
2. **Handlers:** files in `plugins/` receive private-chat messages, callback queries, commands, settings, screenshots, and administration actions.
3. **Intake and settings:** the video handler validates the attachment, checks limits, reserves disk, downloads the source, and queues an encode. User settings are normalized defensively in `bot/utils/settings.py`.
4. **Encoding:** `bot/func/queue_manager.py` schedules work; `bot/func/encode.py` owns FFmpeg process control, progress, cancellation, output checks, thumbnails, and delivery; `bot/func/ffmpeg_utils.py` builds the FFmpeg command graph.
5. **Delivery:** `bot/func/upload_manager.py` limits concurrent uploads. A delivery failure or restart can leave a restart-safe recovery manifest in the download directory.
6. **Persistence:** `database.py` uses Motor/MongoDB for user settings, statistics, queue snapshots, bans, admins, maintenance state, and privacy tombstones.
7. **Health server:** `bot/server.py` serves `GET /` and `GET /healthz`; it does not implement Telegram transport.

The Pyrofork dependency is pinned to a repository commit in `requirements.txt`. The code imports the Pyrofork-compatible `pyrogram` package interface exposed by that dependency. This is MTProto client architecture; it is not an HTTP request to `api.telegram.org`.

## Requirements

### Required

- **Python 3.10+**. The repository CI currently uses Python 3.11.
- **FFmpeg and ffprobe** installed and executable. They may be named `ffmpeg`/`ffprobe` or configured with `FFMPEG_BIN` and `FFPROBE_BIN`.
- **A reachable MongoDB deployment** and permission to ping it and create the privacy-tombstone TTL index.
- **Telegram credentials:** a bot token, API ID, API hash, and the numeric Telegram user ID of the bot owner.
- **A negative Telegram supergroup/channel ID** for the configured log channel. The bot needs appropriate send/copy/pin permissions for the optional logging and broadcast features.
- **Network access** to Telegram and MongoDB. Network access and Git are also required when `UPDATE_ON_START=1`.

### FFmpeg capabilities

Startup validates the FFmpeg build before accepting traffic. The configured executable must provide:

- the `drawtext`, `scale`, and `overlay` filters;
- the `libx264`, `libx265`, `libvpx-vp9`, and `libaom-av1` encoders;
- `fps_mode` support; and
- a working `ffprobe` executable.

The Docker image installs a checksum-verified FFmpeg build and currently declares checksums for x86-64 and ARM64. A local build may use another distribution package as long as it passes the same capability checks.

## Setup

### 1. Obtain Telegram and database credentials

1. Create the bot with BotFather and obtain its bot token.
2. Create an application at Telegram's API development portal and obtain the API ID and API hash.
3. Identify the bot owner's numeric Telegram user ID. This is the value of `OWNER_ID`; owner-only checks use this number.
4. Create or choose a private supergroup/channel for operational messages. Telegram supergroup and channel IDs are normally negative values such as `-1001234567890`.
5. Provision MongoDB. The database user must be able to access the configured database and create indexes. The bot creates a TTL index for privacy tombstones during startup.

These are deployment prerequisites, not a claim that a live Telegram or MongoDB deployment was exercised while preparing this README.

### 2. Clone and install Python dependencies

```bash
git clone https://github.com/Itzmepromgitman/ArgonsEncoder.git
cd ArgonsEncoder

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell, use the equivalent virtual-environment commands:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` includes the pinned Pyrofork Git dependency, Motor, dotenv, aiohttp, psutil, and the non-Windows uvloop dependency. Install Git and the FFmpeg tools separately.

### 3. Verify the local toolchain

```bash
ffmpeg -version
ffprobe -version
```

The paths must be visible to the process. If the binaries are elsewhere, set `FFMPEG_BIN` and `FFPROBE_BIN` in `.env`.

### 4. Create `.env`

Create `.env` in the repository working directory. Do not commit it; it is already ignored by Git.

```env
# Telegram / Pyrofork
TG_BOT_TOKEN=replace-with-bot-token
APP_ID=12345678
API_HASH=replace-with-api-hash
OWNER_ID=1234567890
CHANNEL_ID=-1001234567890

# MongoDB
DATABASE_URL=replace-with-mongodb-uri
DATABASE_NAME=Cluster

# Runtime
PORT=8030
TG_BOT_WORKERS=8
MAX_CONCURRENT_TRANSMISSIONS=8
FFMPEG_BIN=ffmpeg
FFPROBE_BIN=ffprobe

# Privacy and logging (opt in deliberately)
LOG_DELIVERIES=0
ERROR_LOGS_TO_TELEGRAM=0

# Leave disabled unless update.py is configured with a trusted full SHA
UPDATE_ON_START=0
```

Use a real MongoDB URI, not the placeholder. `OWNER` is a legacy value in `bot/config.py`; authorization uses the numeric `OWNER_ID`.

### 5. Run locally

On a Unix-like shell:

```bash
bash start.sh
```

`start.sh` verifies both media executables, runs the optional update step, and then executes `python3 -m bot`. On Windows, run the bot from an activated virtual environment with `python -m bot`, or use a Bash-compatible environment for `start.sh`; direct `python -m bot` does not execute the optional `start.sh` update step.

## Configuration

The loader reads `.env` when `bot.config` is imported. Values below are the defaults in this checkout. Several values are clamped to safe minimums or caps when loaded; use the table as the starting point rather than as a substitute for checking the source if you change limits.

### Identity, Telegram, and runtime

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `TG_BOT_TOKEN` | `YOUR_BOT_TOKEN_HERE` | **Required.** Bot token used by the Pyrofork client. |
| `APP_ID` | `12345678` | **Required.** Positive Telegram API application ID. |
| `API_HASH` | `your_api_hash_here` | **Required.** Telegram API hash. |
| `OWNER_ID` | `1234567890` | **Required.** Numeric owner ID; the owner is never subject to a ban. |
| `CHANNEL_ID` | `-1001234567890` | **Required negative ID.** Mapped internally to the operations/log channel. |
| `DATABASE_URL` | placeholder | **Required.** Motor/MongoDB connection URI. |
| `DATABASE_NAME` | `Cluster` | MongoDB database name. |
| `PORT` | `8030` | Health server port; valid range is 1–65535. |
| `BOT_NAME` | `Argons Encoder` | Name shown in bot messages. |
| `BOT_VERSION` | `2.3.0` | Version shown by the About panel. |
| `TG_BOT_WORKERS` | `8` | Pyrofork event workers; this is separate from encoding worker slots. |
| `MAX_CONCURRENT_TRANSMISSIONS` | `8` | Pyrofork concurrent transmission limit. |

### Concurrency, input, and output limits

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `MAX_CONCURRENT_JOBS` | `4` | FFmpeg encoding slots. |
| `MAX_CONCURRENT_DOWNLOADS` | `4` | Concurrent source downloads, also used by screenshot work. |
| `MAX_CONCURRENT_UPLOADS` | `2` | Concurrent delivery workers. |
| `MAX_JOBS_PER_USER` | `5` | Active encoding/delivery cap per user. |
| `MAX_QUEUE_LENGTH` | `500` | Global encoding queue cap; minimum accepted value is 10. |
| `MAX_FILE_SIZE` | `2147483648` | Maximum source size in bytes (2 GiB by default). |
| `MAX_MEDIA_DURATION` | `21600` | Maximum source duration in seconds (6 hours by default; minimum 60). |
| `MIN_FREE_DISK_BYTES` | `2147483648` | Free-space floor checked before intake and reservations (2 GiB by default). |
| `MAX_OUTPUT_SIZE` | `4294967296` | Per-output cap in bytes (4 GiB by default); FFmpeg also receives `-fs`. |
| `MAX_OUTPUT_VARIANTS` | `4` | Expected maximum output variants used for intake reservation planning. |
| `MIN_OUTPUT_RESERVATION_BYTES` | `268435456` | Minimum per-variant reservation floor (256 MiB by default; minimum 64 MiB). |
| `FFMPEG_THREADS` | CPU cores ÷ `MAX_CONCURRENT_JOBS` | FFmpeg thread count, capped at 16. Set explicitly when tuning a shared host. |
| `FFMPEG_WALL_TIMEOUT` | `28800` | Maximum encode wall-time budget in seconds (8 hours by default; minimum 60). |
| `PAUSED_JOB_TTL` | `7200` | Retention period for a yielded/paused process in seconds (2 hours by default; minimum 300). |
| `UPLOAD_RETRY_CAP` | `1000` | Soft recovery-entry threshold. The code warns at the threshold and retains entries rather than silently truncating them. |
| `UPLOAD_RETRY_PER_USER` | `3` | Maximum recovery entries retained for one user. |
| `MAX_RECOVERY_DISK_BYTES` | `21474836480` | Physical/reserved recovery-storage budget (20 GiB by default). |
| `SETTINGS_CACHE_MAX` | `2000` | Bounded in-process settings-cache size; minimum 100. |

### Paths, logging, and UI

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `DOWNLOAD_DIR` | `downloads` | Downloaded sources, encoded outputs, and `.upload_retries.json`. |
| `THUMB_DIR` | `thumbs` | Custom, generated, and delivery-normalized thumbnails. |
| `WATERMARK_DIR` | `watermarks` | Watermark images, fonts, and preview files. |
| `FONT_PATH` | `bot/fonts/Roboto-Regular.ttf` | Default font used for text watermarks and previews. |
| `LOG_DIR` | `logs` | Rotating log directory; the current file is `bot.log`. |
| `FFMPEG_BIN` | `ffmpeg` | FFmpeg executable name or path. |
| `FFPROBE_BIN` | `ffprobe` | ffprobe executable name or path. |
| `UI_UPDATE_INTERVAL` | `3.0` | Minimum interval for visible FFmpeg progress-card edits; minimum 0.5 seconds. |
| `PROGRESS_CALLBACK_INTERVAL` | `3.0` | Defined in `bot/config.py`; the current visible progress path is controlled by `UI_UPDATE_INTERVAL`. |
| `SAVE_DEBOUNCE_SECONDS` | `2.0` | Debounce delay for ordinary queue snapshots in MongoDB. |

### Feature flags and updates

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `LOG_DELIVERIES` | `0` | When `1`, sends new-user/intake notices and copies delivered media to `CHANNEL_ID`. Keep `0` for the most private default. |
| `ERROR_LOGS_TO_TELEGRAM` | `0` | When `1`, sends error notifications to `CHANNEL_ID`. File logs remain enabled independently. |
| `UPDATE_ON_START` | `0` | When `1`, `start.sh` runs `update.py` before starting the bot. |
| `UPDATE_COMMIT` | empty | Required when auto-update is enabled; must be a full 40-character commit SHA. |
| `UPSTREAM_REPO` | `https://github.com/Itzmepromgitman/ArgonsEncoder.git` | Repository used by `update.py`. |

Auto-update is a working-tree replacement, not a library dependency resolver: `update.py` removes any local `.git`, initializes a local repository, commits the current tree, fetches the selected upstream commit, hard-resets to it, and reinstalls requirements when that file changes. Use a trusted pinned SHA and understand this behavior before enabling it. For immutable/container deployments, building a reviewed image is generally easier to reason about.

## Accepted media and limits

The intake handler is private-chat only. It accepts a video message or a document whose extension is one of the following, or whose MIME type starts with `video/`:

- `.mp4`, `.avi`, `.mov`, `.mkv`, `.wmv`, `.flv`, `.webm`, `.m4v`
- `.3gp`, `.ogv`, `.ts`, `.mts`, `.m2ts`
- `.vob`, `.asf`, `.rm`, `.rmvb`

Before downloading, the handler checks the configured source-size and duration limits, duplicate source ID, queue caps, free disk, and the disk reservation. The screenshots command uses the same broad media guardrails and samples frames across the source timeline.

The bot does not claim that every FFmpeg build can decode every container. `ffprobe` and FFmpeg failures are surfaced as job errors, and the user can send a different source or choose a compatible delivery mode.

## Commands

Commands are generally intended for private chats. The Telegram command menu is populated during startup; the table also includes the accepted handler alias `/u_setting`.

| Command | Description | Access |
| :--- | :--- | :--- |
| `/start` | Open the home menu and register a new user profile. | Everyone |
| `/settings`, `/u_setting` | Configure video, audio, subtitles, trim/sample, branding, metadata, output, and presets. | User |
| `/queue` | View the caller's encoding jobs; the owner sees the fleet queue. | User / owner |
| `/status` | Show the caller's jobs plus CPU, RAM, disk, and uptime; the owner sees fleet status. | User / owner |
| `/stats` | Show persisted global encode statistics and processed/produced bytes. | Everyone |
| `/ss` | Reply to a video to extract timeline screenshots. | User |
| `/cancel <job_id>` | Cancel one of the caller's jobs; the owner may cancel any encoding job. | User / owner |
| `/clear` | Clear the caller's queued encoding jobs; the owner is offered a confirmed fleet-wide choice. | User / owner |
| `/forget` | Request deletion of the caller's settings, assets, statistics, queued work, and recovery entries. | User |
| `/features` | Show the bot's feature overview. | Everyone |
| `/help` | Show the in-bot usage manual. | Everyone |
| `/jobs` | Fleet-wide encoding dashboard with per-job cancel actions. | Owner |
| `/info <job_id>` | Show detailed job, user, file, and status information. | Owner |
| `/cancelall` | Confirm and cancel all active encoding jobs. | Owner |
| `/admin` | Open the owner administration panel and manage extra admins. | Owner |
| `/broadcast` | Reply to a message, then choose normal delivery or a pinned copy in the log channel. | Admin |
| `/ban <user_id>` | Ban a numeric ID or replied user. | Admin |
| `/unban <user_id>` | Remove a numeric ID from the ban list. | Admin |
| `/maint` | Toggle maintenance intake. Existing jobs are allowed to finish. | Admin |
| `/restart` | Persist queue/upload state and restart the bot process; may run the configured updater. | Owner |
| `/log` | Send the current rotating log file. | Owner |
| `/shell` | Execute Python supplied in a reply (message text or `.py` document). | Owner |

### Inline controls

The progress card also provides Pause/Resume, Cancel, and Queue actions. Queue cards provide numbered per-job cancellation and refresh. A failed delivery provides a Details/Retry action when a recovery entry remains available. Callback ownership and job ownership are checked before these actions are accepted.

Admin and ban data are stored in MongoDB, not hard-coded in the README. The owner panel manages the extra-admin list; the owner is always authorized.

## Safety and privacy

### Data boundaries

- **Private-chat processing:** command, settings, media, screenshot, and callback handlers are scoped to private chats. Queue/status callbacks verify that the panel belongs to the requesting user.
- **Local media:** source files, encoded outputs, recovery manifests, thumbnails, and watermark assets are written under the configured runtime directories. They are not automatically kept in an encrypted store by this project; protect the host, volumes, and backups.
- **MongoDB:** user settings, statistics, queue snapshots, admin/ban/maintenance state, and privacy tombstones are stored in MongoDB. The database URI can contain credentials and must be treated as a secret.
- **Telegram delivery:** encoded media is sent with content protection enabled. Delivery remains subject to the receiving chat/client's Telegram behavior and permissions.
- **Session credentials:** protect the Pyrofork session file (`bot_session.session` and its journal) as well as the bot token, API hash, and MongoDB URI. The repository ignores these files from Git.

### Deletion and privacy tombstones

`/forget` is serialized against new queue and delivery admission. It cancels active/queued work, removes saved settings, personal watermark/thumbnail assets, user statistics, queue rows, and recovery entries, and writes durable pending/complete privacy markers. A 30-day MongoDB TTL tombstone prevents a late worker from recreating a profile while deletion is being coordinated.

If any durable cleanup or persistence step fails, the bot does **not** claim that deletion completed; it retains tracking where necessary and asks the user to try again. `/start` can create a fresh profile after a completed deletion, so the tombstone is a race-protection mechanism rather than a permanent account block.

### Opt-in channel logging

`LOG_DELIVERIES=0` is the privacy-safe default. Setting it to `1` enables channel messages for new users and accepted media, and copies delivered media to `CHANNEL_ID`. `ERROR_LOGS_TO_TELEGRAM=0` keeps error notifications local; setting it to `1` sends error messages to that channel. Both can expose user IDs, filenames, operational details, or media to channel members, so grant the bot only the permissions it needs and document the retention policy of that channel.

### Bounded overrides and dangerous operations

- Custom FFmpeg entries are validated against a bounded allowlist. Paths, protocols, input/output selection, stream mapping, and unbounded resource flags are rejected.
- `/shell` executes arbitrary Python in the bot process. Keep `OWNER_ID` private, restrict who can message the bot, and treat the owner account as a production root credential.
- `/restart` replaces the running process. `UPDATE_ON_START=1` adds a network fetch and a hard reset to a selected upstream commit; pin it to a reviewed commit or rebuild the deployment image instead.
- Logs and recovery files can contain filenames, user IDs, Telegram errors, or encoded media. Do not publish the runtime directories.

## Reliability, recovery, and operations

### Startup and shutdown

Startup performs configuration validation, starts the Pyrofork client, pings MongoDB, ensures the privacy TTL index, validates FFmpeg capabilities, performs startup cleanup, starts the health server, restores the MongoDB queue, and restores the local upload-recovery manifest. A failure in the database readiness check, privacy-index setup, FFmpeg validation, queue restoration, or recovery-manifest restoration causes startup to stop rather than accepting work with unknown state.

The health endpoint is intentionally shallow:

```bash
curl http://127.0.0.1:8030/healthz
```

It returns a JSON `status` and `service` value and also responds at `/`. The endpoint itself does not perform a fresh Telegram or MongoDB query; MongoDB and FFmpeg are checked during startup.

Before a normal `/restart` or process shutdown, the bot tries to persist the queue snapshot and upload-recovery manifest. If the required state cannot be persisted, the restart is aborted or the old process is kept alive where the code can recover safely. After a successful replacement, running/yielded queue rows are restored as pending work and rebuilt from the original Telegram source message.

### Queue recovery

- Queue rows are stored as primitive/BSON-safe data in the MongoDB `config` collection under `queue_state`; callables and arbitrary function arguments are not persisted.
- Ordinary snapshots are debounced by `SAVE_DEBOUNCE_SECONDS`; shutdown/restart paths force a snapshot.
- On restore, encode workers fetch the original private Telegram message and download the source again. If that message was deleted or cannot be read, the user is told to send the file again.
- Jobs that exceed current queue, per-user, or disk-reservation limits remain deferred instead of being discarded. Deferred rows are retried when capacity becomes available and during the periodic recovery loop.
- Duplicate detection prefers Telegram file IDs, with a filename fallback for legacy rows.

### Upload recovery

- Active and pending delivery metadata is written atomically to `DOWNLOAD_DIR/.upload_retries.json`; output files referenced by the manifest are preserved during startup cleanup.
- A failed upload can leave the undelivered output on disk with a Details/Retry message. Recovery entries expire after 24 hours unless they are claimed earlier.
- The retry claim is tied to the original user and is not silently handed to another account.
- Recovery is bounded by per-user entries, a total recovery-entry threshold, physical output size, reserved bytes, and `MAX_RECOVERY_DISK_BYTES`. When a recovery limit is reached, the code removes undelivered output where possible to protect storage; failed removals remain tracked for cleanup.
- `/queue` can cancel pending delivery work for the caller. Successful delivery removes the local output; the final manifest persistence is still attempted.

### Resource and failure safeguards

- Disk checks happen before intake and reservations account for estimated source and output space.
- FFmpeg receives a bounded thread count, a wall-time limit, and a per-output size cap. A timeout or failed process is converted into a user-visible failure with bounded technical details.
- Paused processes are retained for `PAUSED_JOB_TTL`; a periodic cleanup cancels and releases expired paused work.
- FFmpeg stdout progress is continuously read and stderr is drained into a bounded tail so long encodes do not deadlock on a full pipe.
- Cancellation terminates the parent and child processes, releases reservations when safe, and retains a cleanup marker if a file cannot be removed.
- The rotating log handler writes up to approximately 5 MB per file with three backups under `logs/`.

### Operator recovery checklist

1. Check container/service status and inspect `logs/bot.log` before changing state.
2. Check free disk space and the configured `DOWNLOAD_DIR`, `WATERMARK_DIR`, and `THUMB_DIR` permissions.
3. Confirm the health endpoint responds; remember that it is not a fresh dependency check.
4. Use `/queue` and the user-owned Retry/Details actions instead of manually deleting active files.
5. Do not delete `.upload_retries.json` or its referenced outputs while recovery entries are pending unless you intentionally accept losing restart recovery.
6. If a restored job cannot find its original Telegram message, ask the user to resend the source.
7. For a failed `/restart`, inspect the queue/recovery logs and keep the old process until the persisted state is understood.
8. Rebuild or pin a known commit before enabling automatic updates; never use an untrusted `UPDATE_COMMIT`.

## Testing and validation

The repository includes unit tests, command-generation checks, syntax checks, and Ruff checks in `.github/workflows/lint.yml`. The suite is designed to run without a real Telegram account and is not a live Telegram/MongoDB integration test. No claim of live Telegram or MongoDB verification is made here.

Install the CI-only tools and run the same checks locally:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install ruff==0.16.8 pytest==9.1.1

python -m compileall -q bot plugins database.py update.py verify_ffmpeg_cmd.py
ruff check . --select E9,F63,F7,F81,F82,F821,F822,F823,F401
python verify_ffmpeg_cmd.py
pytest tests/ -q
```

For PowerShell, set the small test environment variables in the current shell before running the commands:

```powershell
$env:TG_BOT_TOKEN = "test"
$env:APP_ID = "1"
$env:API_HASH = "test"
$env:DATABASE_URL = "mongodb://localhost:27017"
```

What the checks cover:

- `verify_ffmpeg_cmd.py` smoke-tests generated command shapes for single/multi-resolution output, codec flag mapping, audio stripping, trim/sample flags, and remux behavior.
- `tests/test_ffmpeg_utils.py` covers command generation, no-upscale scaling, stream mapping, subtitles, thumbnails, rename/custom-argument validation, and MP4 settings.
- `tests/test_queue_job.py` checks primitive-only queue serialization and restoration.
- `tests/test_settings_utils.py` checks defensive settings normalization and quality-preset behavior.
- The remaining tests cover UI/format helpers, listener coordination, FFmpeg process startup contracts, callback contracts, and Mongo value serialization.

A production smoke check is still required before exposing a deployment: configure a real Telegram bot and MongoDB in a controlled environment, send a small supported video, and verify the resulting queue/delivery behavior. That operational check is separate from the repository test suite.

## Deployment

### Docker

The included `Dockerfile` uses Fedora 43, installs a checksum-verified FFmpeg build, runs the bot as the non-root `botuser` (UID 1000), and defines a health check against `$PORT`. `.env` is excluded from the image; pass it at runtime.

```bash
docker build -t argonsencoder:local .

docker run -d \
  --name argonsencoder \
  --restart unless-stopped \
  --env-file .env \
  -p 8030:8030 \
  -v argons_downloads:/bot/downloads \
  -v argons_thumbs:/bot/thumbs \
  -v argons_watermarks:/bot/watermarks \
  -v argons_logs:/bot/logs \
  argonsencoder:local
```

The `downloads` volume is especially important for `.upload_retries.json` and recovery outputs. The thumbnail and watermark volumes preserve personal assets across container replacement. The logs volume makes operator diagnostics available after replacement. If you change the directory variables, mount the corresponding container paths instead of the defaults. Bind mounts must be writable by UID 1000.

If `PORT` is changed in `.env`, expose the same container port and update the host mapping. Inspect the container without exposing the bot to the public internet unnecessarily:

```bash
docker ps --filter name=argonsencoder
curl http://127.0.0.1:8030/healthz
docker logs --tail 200 argonsencoder
```

Provide MongoDB through `DATABASE_URL`; it is not bundled into the image. The health endpoint is a liveness/keep-alive signal, while startup performs the MongoDB ping and FFmpeg capability validation.

### Updating a deployment

The default is `UPDATE_ON_START=0`. To use the built-in updater, configure a reviewed full commit SHA:

```env
UPDATE_ON_START=1
UPDATE_COMMIT=<40-character-commit-sha>
UPSTREAM_REPO=https://github.com/Itzmepromgitman/ArgonsEncoder.git
```

`update.py` runs before the bot only when `start.sh` is used. It replaces the working tree, so do not place local-only changes in an auto-updating deployment. For Docker or other immutable deployments, prefer building a new image from a reviewed commit, stopping the old container, and starting the new one with the same persistent volumes.

## Project structure

```text
ArgonsEncoder/
├── bot/
│   ├── __main__.py             # Pyrofork client, startup/readiness, restoration
│   ├── config.py               # Environment configuration and validation
│   ├── decorator.py            # Ban gate, error handling, task wrapper
│   ├── logger.py               # Rotating file logs and optional Telegram errors
│   ├── server.py               # / and /healthz JSON endpoint
│   ├── fonts/
│   │   └── Roboto-Regular.ttf  # Default watermark font
│   ├── func/
│   │   ├── download_manager.py  # Download semaphore
│   │   ├── editquery.py         # Encoding callbacks and queue rendering
│   │   ├── encode.py            # FFmpeg process, delivery, recovery
│   │   ├── ffmpeg_utils.py      # Command graph, validation, branding
│   │   ├── media.py             # Telegram thumbnail normalization
│   │   ├── preview.py           # Watermark preview generation
│   │   ├── queue_manager.py     # Persistent encoding queue
│   │   ├── upload_manager.py    # Persistent delivery workers
│   │   └── pyroutils/           # Progress formatting and callbacks
│   └── utils/
│       ├── format.py            # Byte/time formatting
│       ├── listener.py          # One-prompt-per-user input coordination
│       ├── restart.py           # Safe restart/update flow
│       ├── settings.py          # Defaults, normalization, quality presets
│       ├── shell.py             # Owner-gated Python execution
│       └── ui.py                # Shared cards, buttons, and progress bar
├── plugins/
│   ├── admin.py                 # Owner/admin operations and broadcast
│   ├── encode.py                # Private video intake
│   ├── query.py                 # Encoding callback routing
│   ├── queue.py                 # User and owner queue/status views
│   ├── screenshot.py            # Timeline frame extraction
│   ├── settings.py              # Interactive settings UI
│   └── start.py                 # Home/help/features/profile menus
├── tests/                       # Unit and contract tests
├── database.py                  # Motor/MongoDB access and privacy deletion
├── update.py                    # Optional pinned upstream updater
├── verify_ffmpeg_cmd.py         # FFmpeg command-generator smoke script
├── Dockerfile
├── AUTHORS.md
├── requirements.txt
├── start.sh
└── README.md
```

## License and credits

This project is licensed under the **GNU General Public License v3.0**. See [`LICENSE`](LICENSE) for the full terms.

**Developed by ARGON telegram: @REACTIVEARGON**<br>
Telegram: [@REACTIVEARGON](https://t.me/ReactiveArgon)

<div align="center">
  <br>
  <i>Built for careful encoding, recoverable work, and private Telegram workflows.</i>
</div>
