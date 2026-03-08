# 🎵 Discord Music Bot

A fully-featured Discord music bot built with Python. Supports YouTube and Spotify playback, queue management, audio effects, and lyrics — all through simple text commands.

---

## ✨ Features

- 🎬 **YouTube** — Play songs by name or URL, including full playlists
- 🟢 **Spotify** — Play tracks, albums, and playlists from Spotify links
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
| `!play <song/url>` | Play a song from YouTube or Spotify (track, album, playlist) |
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
