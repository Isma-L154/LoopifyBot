"""
Commands only ever run inside a server.

Every command acts on a guild's voice connection or queue, and a DM has
neither: before :func:`guild_only` was registered, ``!play`` sent in a DM died
on ``ctx.author.voice`` and answered with a generic error.

Because the check guarantees a guild, :class:`GuildContext` can tell the type
checker so. It exists only for type checking — at runtime it *is*
``commands.Context`` — which is what lets command bodies use ``ctx.guild.id``
without a ``None`` test the check has already made impossible.
"""

from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional

import discord
from discord.ext import commands

if TYPE_CHECKING:
    class GuildContext(commands.Context[commands.Bot]):
        @property
        def guild(self) -> discord.Guild: ...

        @property
        def author(self) -> discord.Member: ...

        @property
        def me(self) -> discord.Member: ...

        @property
        def voice_client(self) -> Optional[discord.VoiceClient]: ...

    # mypy cannot solve commands.hybrid_command's union of Concatenate callback
    # types when the callback is a cog method (it can for a plain function, and
    # pyright can for both). This is the same decorator with a signature mypy
    # accepts; at runtime it is commands.hybrid_command itself.
    def hybrid_command(
        name: str = ..., **attrs: Any,
    ) -> Callable[[Callable[..., Coroutine[Any, Any, None]]],
                  commands.HybridCommand[Any, ..., None]]: ...
else:
    GuildContext = commands.Context
    hybrid_command = commands.hybrid_command


async def guild_only(ctx: commands.Context[Any]) -> bool:
    """Global check: refuse anything sent outside a server."""
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    return True
