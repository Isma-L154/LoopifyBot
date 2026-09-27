"""
Playback actions shared by commands and the Now Playing buttons.

Both surfaces do the same thing and must say the same thing; only how the
answer is delivered differs — an embed for a command, an in-place edit or an
ephemeral reply for a button. So each action returns an :class:`Outcome` and
leaves the delivery to its caller.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from utils.player import MusicPlayer

LoopMode = Literal["off", "track", "queue"]

LOOP_ICONS: dict[str, str] = {"off": "➡️", "track": "🔂", "queue": "🔁"}
_NEXT_LOOP: dict[str, LoopMode] = {"off": "track", "track": "queue", "queue": "off"}

NOTHING_PLAYING = "Nothing is playing."
STOPPED = "Disconnected and cleared the queue."


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str


def pause(player: Optional["MusicPlayer"]) -> Outcome:
    if player and player.pause():
        return Outcome(True, "Paused ⏸")
    return Outcome(False, "Nothing is playing right now.")


def resume(player: Optional["MusicPlayer"]) -> Outcome:
    if player and player.resume():
        return Outcome(True, "Resumed ▶️")
    return Outcome(False, "Nothing is paused.")


def skip(player: Optional["MusicPlayer"]) -> Outcome:
    if player and player.skip():
        return Outcome(True, "Skipped ⏭")
    return Outcome(False, NOTHING_PLAYING)


def previous(player: Optional["MusicPlayer"]) -> Outcome:
    if player and player.go_previous():
        return Outcome(True, "Playing previous track ⏮")
    return Outcome(False, "No previous track in history.")


def shuffle(player: Optional["MusicPlayer"]) -> Outcome:
    if not player or player.is_empty:
        return Outcome(False, "Queue is empty.")
    player.shuffle()
    return Outcome(True, "Queue shuffled 🔀")


def set_loop(player: Optional["MusicPlayer"], mode: LoopMode) -> Outcome:
    if not player:
        return Outcome(False, NOTHING_PLAYING)
    player.loop_mode = mode
    return Outcome(True, f"Loop mode set to **{mode}** {LOOP_ICONS[mode]}")


def cycle_loop(player: Optional["MusicPlayer"]) -> Outcome:
    if not player:
        return Outcome(False, NOTHING_PLAYING)
    return set_loop(player, _NEXT_LOOP[player.loop_mode])


def stop(player: Optional["MusicPlayer"]) -> Outcome:
    if not player:
        return Outcome(False, NOTHING_PLAYING)
    player.destroy()
    return Outcome(True, STOPPED)
