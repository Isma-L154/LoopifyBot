# 🎵 Discord Music Bot

A fully-featured Discord music bot built with Python. Supports YouTube and SoundCloud playback, queue management, audio effects, and lyrics — all through simple text commands.

---

## ✨ Features

- 🎬 **YouTube** — Play songs by name or URL, including full playlists
- 🟠 **SoundCloud** — Play tracks, sets and search with the `sc:` prefix
- 🔗 **Direct links** — Any of the 1000+ sites yt-dlp supports, plus raw audio URLs
- 📋 **Queue system** — Full queue management with shuffle, loop, and history
- 🎛️ **Audio effects** — Bass boost, nightcore, vaporwave, 8D audio, echo, and more
- 🎤 **Live lyrics** — One message that follows the song line by line, karaoke-style
- 🎛️ **Buttons** — Pause, skip, previous, stop, loop, shuffle, queue and lyrics right under Now Playing
- ⚡ **Slash commands** — Every command also works as `/command`, with search suggestions in `/play`
- 🔊 **Volume control** — Per-server volume adjustment
- 🔁 **Loop modes** — Loop a single track or the entire queue
- ▶️ **Autoplay** — Automatically queue related tracks when the queue ends

---

## 📋 Commands

Every command below also works as a slash command: `/play`, `/skip`, `/effect`…
Aliases (`!p`, `!q`, `!bass`…) are `!`-only.

Each **Now Playing** message carries buttons for anyone in the bot's voice
channel: ⏮ ⏯ ⏭ ⏹ on the first row, 🔁 (cycle loop) 🔀 (shuffle) 📜 (queue,
shown only to you) 🎤 (live lyrics) on the second. When the next song starts,
the previous message's buttons are disabled.

### ▶️ Playback

| Command | Description |
|---|---|
| `!play <song/url>` | Play from YouTube/SoundCloud (search, track, playlist) or a link |
| `!pause` | Pause the current track |
| `!resume` | Resume playback |
| `!skip` | Skip to the next track |
| `!previous` | Go back to the previous track |
| `!stop` | Stop playback and disconnect the bot |
| `!nowplaying` | Show the currently playing track |

### 📋 Queue

| Command | Description |
|---|---|
| `!queue [page]` | Display the current queue |
| `!shuffle` | Shuffle the queue |
| `!remove <#>` | Remove a track by its position |
| `!move <from> <to>` | Move a track to a different position |
| `!clear` | Clear the entire queue |
| `!loop <track\|queue\|off>` | Set the loop mode |
| `!autoplay` | Toggle autoplay on/off |

### 🔊 Audio

| Command | Description |
|---|---|
| `!volume <0-100>` | Set the playback volume |
| `!bass` | Apply a light bass boost |
| `!bassboost` | Apply a heavy bass boost |
| `!nightcore` | Speed up and raise pitch (nightcore effect) |
| `!vaporwave` | Slow down and lower pitch (vaporwave effect) |
| `!treble` | Boost treble frequencies |
| `!echo` | Add an echo effect |
| `!8d` | Apply 8D audio (use headphones!) |
| `!karaoke` | Remove center vocals |
| `!reset` | Remove all audio effects |
| `!effect [name]` | Show the active effect, or apply one by name (`/effect` lists them) |
| `!effects` | List all available effects |

### 🎤 Extras

| Command | Description |
|---|---|
| `!lyrics` | Follow the current song's lyrics live, in one self-updating message |
| `!lyrics <title>` | Look up lyrics by song title |
| `!lyrics <title> - <artist>` | Look up lyrics by title and artist |
| `!help` | Show the full command list |

---

## ➕ Add to your server

**[Click here to invite the bot](https://discord.com/oauth2/authorize?client_id=1411151372446863491&permissions=36784128&integration_type=0&scope=bot+applications.commands)**

No installation required — the bot is hosted and always online.

---

## 🛠️ Self-hosting

### Requirements
- Python 3.11+
- FFmpeg on your PATH
- A Discord bot token with the **Message Content** intent enabled — the only
  privileged intent the bot asks for

### Run locally
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
cp .env.example .env                            # then fill in DISCORD_TOKEN
python main.py
```

Lyrics need no credentials: timings come from [LRCLIB](https://lrclib.net),
which is free and needs no key. `GENIUS_TOKEN` in `.env` is optional and only
adds a fallback for songs LRCLIB does not have.

### Running the tests
```bash
.venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\pip
pytest
ruff check .      # correctness lint (ruff.toml)
mypy              # every function must be typed (mypy.ini)
```

The suite needs **no credentials, no network and no `.env`** — it mocks the
Discord gateway and never calls yt-dlp. It covers queue and player state, the
`_advance` state machine (loop modes, skip, replay, autoplay, idle timeout), the
read-ahead buffer and stream teardown, the `services.media` helpers, the
voice-state guards, the command edge cases, the generated `!help`, the Genius
client, and startup/shutdown behaviour — including that a login survives a DNS
outage and that the process stops inside the time systemd allows.

A few tests render audio through **real FFmpeg** to measure the pitch and speed
filters, since a wrong filter string looks perfectly reasonable and only shows up
as the wrong playback speed. Those skip automatically if FFmpeg is not installed.

CI runs the lint, the type check and the tests on every push and pull request,
against Python 3.11 and 3.12.

### Deploy to a server
See **[deploy/README.md](deploy/README.md)** for the full walkthrough:
provisioning (`deploy/setup.sh`), a `systemd` service that auto-restarts and
starts on boot, self-updating with `deploy/update.sh`, and a daily timer that
keeps `yt-dlp` current.

The hosted instance runs on a **self-hosted Linux box on a residential
connection**, not a cloud VM. That is a YouTube decision, not a cost one: from a
datacenter IP the bot needed a cookies file that expired every few weeks, and
from a residential IP the same requests work with none. Deploying to a cloud VM
is still supported and documented, with that caveat.
