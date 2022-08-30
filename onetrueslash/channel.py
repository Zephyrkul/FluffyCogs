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

    def permissions_for(self, obj: discord.abc.Snowflake, /) -> discord.Permissions:
        ctx = contexts.get()
        interaction = ctx._interaction
        assert ctx.bot.user
        if obj.id == interaction.user.id:
            return ctx.permissions
        elif obj.id == interaction.client.user.id:
            return ctx.bot_permissions
        else:
            return super().permissions_for(obj)  # type: ignore

    async def send(self, *args, **kwargs):
        ctx = contexts.get()
        interaction = ctx._interaction
        if interaction.is_expired() and ctx._first_response:
            assert interaction.channel_id
            kwargs["reference"] = discord.MessageReference(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                message_id=ctx._first_response,
                fail_if_not_exists=False,
            )
            return await interaction.channel.send(*args, **kwargs)  # type: ignore
        await self.typing()
        ctx._deferring = False
        delete_after = kwargs.pop("delete_after", None)
        for key in INCOMPATABLE_PARAMETERS_DISCARD:
            kwargs.pop(key, None)
        m = await interaction.followup.send(*args, **kwargs)
        ctx._first_response = min(filter(None, (ctx._first_response, m.id)))
        if delete_after is not None and not m.flags.ephemeral:
            await m.delete(delay=delete_after)
        return m

    def typing(self) -> Thinking:
        return Thinking()

    if hasattr(discord.abc.Messageable, "trigger_typing"):
        trigger_typing = typing
