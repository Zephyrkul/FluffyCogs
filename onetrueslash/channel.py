import inspect

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
        await self.trigger_typing()
        ctx._deferring = False
        interaction = ctx.interaction
        delete_after = kwargs.pop("delete_after", None)
        for key in INCOMPATABLE_PARAMETERS_DISCARD:
            kwargs.pop(key, None)
        m = await interaction.followup.send(*args, **kwargs)
        if delete_after is not None:
            await m.delete(delay=delete_after)
        return m

    async def trigger_typing(self) -> None:
        ctx = contexts.get()
        if not ctx._deferring and not ctx.interaction.response.is_done():
            ctx._deferring = True
            await ctx.interaction.response.defer(ephemeral=True)

    def typing(self):
        return Thinking(self)
