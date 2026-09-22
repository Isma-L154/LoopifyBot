# Synced lyrics — design

`!lyrics` answers with the whole song at once, which for a long track means
several messages in a row (`Rap God` is 8,065 characters — three of them). It is
also static: it says nothing about where the song currently is.

This replaces it with one message that follows the music, and makes the
unsynced case a single navigable message instead of a wall.

## What it does

- `!lyrics` with something playing — posts **one** message showing the line that
  is playing with two lines of context either side, and keeps editing that same
  message as the song moves. It follows the queue: when the next track starts,
  the same message loads its lyrics and carries on.
- `!lyrics <query>` — the lyrics for that search, static, one message, paged
  with buttons when long.
- No synced lyrics available — falls back to plain text in the same paged
  message, labelled so it is clear why it is not moving.
- A **Stop** button ends the follower. So does `!stop`, an empty queue, or the
  player being destroyed.

## Where the timings come from

Genius has no timestamps, so it cannot do this. [LRCLIB](https://lrclib.net) is
free, needs no API key, and returns LRC-format lyrics with `[mm:ss.xx]` marks.
Checked against the music this bot actually plays — KAROL G, Bad Bunny, Queen,
Eminem all returned synced lyrics.

Lookups pass **artist, title and duration**. The duration is the part that
matters: without it a query matches a live version or an extended edit whose
timings are wrong for the audio actually playing. The bot already has it in
`track["duration"]`.

Fallback order: LRCLIB synced → LRCLIB plain → Genius (the existing path).

## Pieces

| Piece | Responsibility |
|---|---|
| `services/synced_lyrics.py` | Talk to LRCLIB; parse LRC into `[(seconds, text)]` |
| `utils/embeds.py` | Render the window and the static pages — pure functions |
| `cogs/lyrics.py` | The command, the follower task, the buttons |

`aiohttp` is used directly rather than `lyricsgenius`. It is already a
dependency of discord.py and it does not block, so the lookup needs no executor
thread — unlike Genius, which does.

**One session, not one per request.** The cog opens an `aiohttp.ClientSession`
on load and closes it on unload. A session per request is what leaked in #34.

## Following the song

A `LyricsFollower` per guild owns one task and one message.

**It does not poll.** It works out when the next line begins and sleeps until
then, capped at 2 seconds so a pause, skip or stop is noticed promptly. That is
at most one wakeup every two seconds, rather than a tight loop — this runs on a
768 MB box.

It edits only when the visible window actually changes, and never more often
than every 2.5 seconds. Discord allows roughly five edits per five seconds per
channel, and fast songs change lines more often than that; the window always
renders the *current* line, so skipping intermediate ones costs nothing.

**No cache.** The follower reloads lyrics only when `player.current` changes
identity, and `loop track` replays the same dict — so a cache would never be
read.

## The speed effects

`nightcore` plays at 1.25x and `vaporwave` at 0.8x, but `MusicPlayer.elapsed` is
wall-clock. At 1.25x the audio is a quarter ahead of the clock, and the lyrics
would visibly drift.

`Effect` gains a `rate` field, `apply_effect` records it, and the player exposes:

```python
position = seek_base + (elapsed - seek_base) * effect_rate
```

`seek_base` is the offset the current stream was spawned at, which is already
what `_start_ts` is backdated by when an effect change resumes in place.

## Failure

Nothing here can take the bot down. LRCLIB unreachable, a malformed LRC body, a
deleted message, a lost permission — each degrades to the static path or ends
the follower quietly, and is logged rather than raised. A missing lyric is not a
reason to stop the music.

## Testing

The parser and the renderer are pure functions and get table tests: multiple
timestamps on one line (`[00:10][01:20] chorus`), `[ar:]`/`[ti:]` metadata,
blank lines between verses, out-of-order marks, a body with no marks at all.

The follower is tested against a fake player and a fake message: that it edits
on a line change, that it does not edit when the window is unchanged, that it
respects the minimum interval, that it follows a track change, that it stops on
destroy, and that it leaves no task behind.

The LRCLIB client is tested against recorded payloads — no network in the suite.

One end-to-end check against the real LRCLIB and the real bot before calling it
done.

## Not doing

- Prefetching the next track's lyrics. One request per track change is already
  cheap, and it would add a second lifecycle to get wrong.
- Translations, romanisation, or contributor credits.
- Per-user followers. One per guild; the second `!lyrics` replaces the first.
