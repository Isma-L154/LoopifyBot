# 🎵 Discord Music Bot

A fully-featured Discord music bot built with Python. Supports YouTube and SoundCloud playback, queue management, audio effects, and lyrics — all through simple text commands.

---

## ✨ Features

- 🎬 **YouTube** — Play songs by name or URL, including full playlists
- 🟠 **SoundCloud** — Play tracks, sets and search with the `sc:` prefix
- 🔗 **Direct links** — Any of the 1000+ sites yt-dlp supports, plus raw audio URLs
- 📋 **Queue system** — Full queue management with shuffle, loop, and history
- 🎛️ **Audio effects** — Bass boost, nightcore, vaporwave, 8D audio, echo, and more
- 🎤 **Lyrics** — Fetch lyrics for any song via Genius
- 🔊 **Volume control** — Per-server volume adjustment
- 🔁 **Loop modes** — Loop a single track or the entire queue
- ▶️ **Autoplay** — Automatically queue related tracks when the queue ends

---

## 📋 Commands

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
| `!effects` | List all available effects |

### 🎤 Extras

| Command | Description |
|---|---|
| `!lyrics` | Get lyrics for the current song |
| `!lyrics <title>` | Search lyrics by song title |
| `!lyrics <title> - <artist>` | Search lyrics by title and artist |
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
- A Discord bot token (with the **Message Content** and **Server Members** intents enabled)

### Run locally
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
cp .env.example .env                            # then fill in DISCORD_TOKEN
python main.py
```

`!lyrics` is optional — set `GENIUS_TOKEN` in `.env` to enable it.

### Running the tests
```bash
.venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\pip
pytest
```

The suite needs **no credentials, no network and no `.env`** — it mocks the
Discord gateway and never calls yt-dlp. It covers queue and player state, the
`_advance` state machine (loop modes, skip, replay, autoplay, idle timeout),
the `services.media` helpers, the voice-state guards, and the command edge
cases. CI runs it on every push and pull request against Python 3.11 and 3.12.

### Deploy to AWS (t4g.micro, ~$6/mo or free tier)
See **[deploy/README.md](deploy/README.md)** for a full walkthrough: launch script,
provisioning (`deploy/setup.sh`) and a `systemd` service that auto-restarts and
starts on boot.
