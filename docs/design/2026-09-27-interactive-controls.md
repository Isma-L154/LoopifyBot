# Interactive controls and slash commands

## Goal

Make the bot usable without typing commands: buttons on the Now Playing
message, and every command available as a `/` command alongside `!`.

## Decisions

- **Hybrid commands.** Every command answers to both `!name` and `/name`.
  Aliases (`!p`, `!q`, `!bass`, …) stay prefix-only, so the `/` menu does not
  fill with duplicates.
- **Buttons.** Two rows on each Now Playing message: `⏮ ⏯ ⏭ ⏹` and
  `🔁 🔀 📜 🎤`. No effects menu.
- **Old messages.** When the next track starts, or the player is destroyed, the
  previous Now Playing message keeps its embed and its buttons are disabled.

## Architecture

### `utils/controls.py` — shared playback actions

Pure functions over `Optional[MusicPlayer]` returning an `Outcome(ok, message)`:
`pause`, `resume`, `toggle_pause`, `skip`, `previous`, `shuffle`, `set_loop`,
`cycle_loop`. They hold the wording ("Nothing is playing.", "Skipped ⏭") in one
place. Commands and buttons both call them and differ only in how they deliver
the result.

### `utils/now_playing_view.py` — the buttons

`NowPlayingControls(discord.ui.View)`, built per announcement with the player,
the track and its requester.

- `interaction_check`: the presser must be in the bot's voice channel, and the
  player must still be the guild's live one. Otherwise an ephemeral refusal.
- `⏯` and `🔁` edit the message in place (the button's emoji, the Loop field).
- `⏭ ⏮ ⏹ 🔀` post a short public line naming who pressed it, because they
  change playback for everyone.
- `📜` answers with the queue, ephemerally.
- `🎤` posts the lyrics to the channel through a `LyricsProvider`, which the
  Lyrics cog registers on load and clears on unload. The view never imports the
  cog. With no provider registered, the button is not added.
- `timeout=None`, so a long track never loses its controls. A view with no
  timeout stays in discord.py's view store until `stop()` is called, so every
  retired view is stopped; otherwise each track played would leak one.

### `utils/announcer.py`

`now_playing` sends the embed with a fresh view and remembers the message.
`retire()` disables and stops the previous view. It runs before each new
announcement and when the player is destroyed.

### Hybrid migration

- `/play` defers first: joining voice can take up to 20s and yt-dlp 3–8s, and
  an interaction must be answered within 3s. `ctx.defer()` does not check
  whether the interaction was already answered, so it is called exactly once;
  `ctx.typing()` does check, and is safe after it.
- `/play` on an idle player replies "Starting …", because the Now Playing
  announcement goes to the channel, and a deferred interaction left without a
  reply shows "thinking…" forever.
- `/loop mode:` is `Literal["track", "queue", "off"]` and `/volume vol:` is
  `commands.Range[int, 0, 100]`. Discord then offers and validates them, and
  under `!` a bad value is a `BadArgument` that `utils.errors` already reports.
- Effects: `!effect` with no argument still shows the active effect;
  `!effect <name>` and `/effect name:` (a choice list built from `EFFECTS`)
  apply one. The `!bass`-style shortcuts are unchanged.
- `/play query:` autocompletes from YouTube's search-suggestion endpoint
  (`services/suggestions.py`). It is unofficial, so it has a 1.5s timeout and
  any failure yields no suggestions rather than an error. URLs are not
  completed.
- Slash commands are registered with Discord by an owner-only, hidden `!sync`
  (`cogs/admin.py`), run once after a deploy that changes commands. Syncing on
  every start would hit Discord's rate limit, and the bot restarts on its own.
- The command tree is declared guild-only (`allowed_contexts`), so `/` commands
  are not offered in DMs at all. The existing `guild_only` check still covers
  `!`.
- Errors and voice-check refusals are sent with `ephemeral=True`: under `/` only
  the person who erred sees them; under `!` the flag is ignored.
- `utils.errors` unwraps `HybridCommandError` as well as `CommandInvokeError`.
- `!help` and the bot's presence mention `/play`.

## Edge cases

| Situation | Behaviour |
|---|---|
| Button pressed by someone outside the bot's voice channel | Ephemeral "join my voice channel" |
| Button pressed after the player was destroyed but before the message was retired | Ephemeral "This player has ended" |
| `⏭` with nothing playing | Ephemeral "Nothing is playing." |
| `⏮` with no history | Ephemeral "No previous track in history." |
| `🔀` on an empty queue | Ephemeral "Queue is empty." |
| `🎤` finds no lyrics | Ephemeral "Couldn't find lyrics" |
| Admin disconnects the bot from voice | Player is destroyed, which retires the message |
| Now Playing channel deleted | Sending and retiring swallow `HTTPException`, as announcing already does |
| Bot restarts | Old messages' buttons answer "interaction failed". Known limitation: no player survives a restart for them to control |
| Suggestion endpoint slow or down | `/play` offers no suggestions and still works |
| `/play` in a DM | Not offered by Discord |

## Testing

Unit tests, no gateway:

- `controls`: every action with no player, idle player and playing player.
- `NowPlayingControls`: the interaction check (outside voice, destroyed
  player), each button's delivery (edit vs public line vs ephemeral), the
  lyrics button's presence with and without a provider.
- `ChannelAnnouncer`: retiring disables and stops the previous view; a failing
  edit is swallowed.
- `suggestions`: parses the endpoint's JSON, returns `[]` on timeout, bad JSON
  and URLs.
- Commands: `/play` defers once and answers on an idle player; `!effect` with and
  without a name; errors unwrap `HybridCommandError`; replies are ephemeral.

Manual, in a server, after `!sync`: each button, the `/` menu with its
choices and autocompletion, and a button pressed from outside voice.
