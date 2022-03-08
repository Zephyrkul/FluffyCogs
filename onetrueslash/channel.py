import inspect
from typing import Optional

import discord

from .utils import Thinking, contexts

INCOMPATABLE_PARAMETERS_DISCARD = frozenset(
    k
    for k in inspect.signature(discord.abc.Messageable.send).parameters
    if k not in inspect.signature(discord.Webhook.send).parameters
)


class InterChannel:
    __slots__ = ()

    async def send(self, *args, **kwargs):
        ctx = contexts.get()
        interaction = ctx.interaction
        await self.trigger_typing(_ephemeral=kwargs.pop("ephemeral", None))
        delete_after = kwargs.pop("delete_after", None)
        kwargs["wait"] = True
        for key in INCOMPATABLE_PARAMETERS_DISCARD:
            kwargs.pop(key, None)
        m = await interaction.followup.send(*args, **kwargs)
        if delete_after is not None:
            await m.delete(delay=delete_after)
        return m

    async def trigger_typing(self, *, _ephemeral: Optional[bool] = None) -> None:
        ctx = contexts.get()
        if not ctx._deferred:
            ctx._deferred = True
            if _ephemeral is None:
                ephemeral = ctx.command_failed or not ctx.command
            await ctx.interaction.response.defer(ephemeral=_ephemeral)

    def typing(self):
        return Thinking(self)
