import inspect
from typing import TYPE_CHECKING

import discord

from .utils import Thinking, contexts

INCOMPATABLE_PARAMETERS_DISCARD = frozenset(
    k
    for k in inspect.signature(discord.abc.Messageable.send).parameters
    if k not in inspect.signature(discord.Webhook.send).parameters
)


class InterChannel(discord.abc.Messageable if TYPE_CHECKING else object):
    __slots__ = ()

    async def send(self, *args, **kwargs):
        ctx = contexts.get()
        if ctx.interaction.is_expired() and ctx._first_response:
            kwargs["reference"] = discord.MessageReference(
                guild_id=ctx.interaction.guild_id,
                channel_id=ctx.interaction.channel_id,
                message_id=ctx._first_response,
                fail_if_not_exists=False,
            )
            return await super().send(*args, **kwargs)
        await self.trigger_typing()
        ctx._deferring = False
        interaction = ctx.interaction
        delete_after = kwargs.pop("delete_after", None)
        for key in INCOMPATABLE_PARAMETERS_DISCARD:
            kwargs.pop(key, None)
        m = await interaction.followup.send(*args, **kwargs)
        ctx._first_response = min(filter(None, (ctx._first_response, m.id)))
        if delete_after is not None:
            await m.delete(delay=delete_after)
        return m

    async def trigger_typing(self) -> None:
        ctx = contexts.get()
        if (
            not ctx._deferring
            and not ctx.interaction.response.is_done()
            and not ctx.interaction.is_expired()
        ):
            ctx._deferring = True
            await ctx.interaction.response.defer(ephemeral=True)

    def typing(self):
        return Thinking(self)
