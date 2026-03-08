import asyncio
import random
from collections import deque
from typing import Optional


class GuildQueue:
    """Manages the music queue for a single Discord guild."""

    def __init__(self):
        self.queue: deque = deque()
        self.history: list = []
        self.current: Optional[dict] = None
        self.loop_mode: str = "off"   # off | track | queue
        self.autoplay: bool = False
        self.volume: float = 0.5
        self._next_event: asyncio.Event = asyncio.Event()

    # ── Queue operations ──────────────────────────────────────────────

    def add(self, track: dict):
        self.queue.append(track)

    def add_next(self, track: dict):
        """Insert a track right after the current one."""
        self.queue.appendleft(track)

    def remove(self, index: int) -> Optional[dict]:
        """Remove track by 1-based index. Returns removed track or None."""
        if index < 1 or index > len(self.queue):
            return None
        lst = list(self.queue)
        removed = lst.pop(index - 1)
        self.queue = deque(lst)
        return removed

    def move(self, from_index: int, to_index: int) -> bool:
        """Move a track from one position to another (1-based)."""
        lst = list(self.queue)
        if not (1 <= from_index <= len(lst)) or not (1 <= to_index <= len(lst)):
            return False
        track = lst.pop(from_index - 1)
        lst.insert(to_index - 1, track)
        self.queue = deque(lst)
        return True

    def shuffle(self):
        lst = list(self.queue)
        random.shuffle(lst)
        self.queue = deque(lst)

    def clear(self):
        self.queue.clear()

    def next_track(self) -> Optional[dict]:
        """Pop the next track respecting loop mode."""
        if self.loop_mode == "track" and self.current:
            return self.current

        if self.current:
            self.history.append(self.current)
            if len(self.history) > 50:
                self.history.pop(0)

        if self.loop_mode == "queue" and self.current:
            self.queue.append(self.current)

        if self.queue:
            self.current = self.queue.popleft()
            return self.current

        self.current = None
        return None

    def previous_track(self) -> Optional[dict]:
        """Go back one track in history."""
        if not self.history:
            return None
        if self.current:
            self.queue.appendleft(self.current)
        self.current = self.history.pop()
        return self.current

    # ── Properties ───────────────────────────────────────────────────

    @property
    def is_empty(self) -> bool:
        return len(self.queue) == 0

    @property
    def size(self) -> int:
        return len(self.queue)

    def to_list(self) -> list:
        return list(self.queue)


class QueueManager:
    """Global manager holding one GuildQueue per Discord guild."""

    def __init__(self):
        self._queues: dict[int, GuildQueue] = {}

    def get(self, guild_id: int) -> GuildQueue:
        if guild_id not in self._queues:
            self._queues[guild_id] = GuildQueue()
        return self._queues[guild_id]

    def delete(self, guild_id: int):
        self._queues.pop(guild_id, None)


# Singleton used across the whole bot
queue_manager = QueueManager()
