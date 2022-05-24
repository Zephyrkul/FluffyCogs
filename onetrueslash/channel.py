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
        assert ctx.bot.user
        if obj.id not in (ctx.interaction.user.id, ctx.bot.user.id):
            return super().permissions_for(obj)  # type: ignore
        if ctx.interaction.is_expired():
            return super().permissions_for(obj)  # type: ignore
        assert ctx.interaction.channel
        channel = ctx.interaction.channel
        if channel.type == discord.ChannelType.private:
            # DMChannel.permissions_for doesn't care about its arguments
            return discord.DMChannel.permissions_for(None, None)  # type: ignore
        if obj.id == ctx.interaction.user.id:
            # bypass all the permissions resolving stuff
            return ctx.interaction.permissions
        guild = ctx.interaction.guild
        if not guild:
            default_perms = discord.Permissions.none()
        else:
            assert not isinstance(channel, discord.PartialMessageable)
            default_perms = channel.permissions_for(guild.default_role)
        my_perms = channel.permissions_for(ctx.me)  # type: ignore
        # webhooks are weird
        my_perms.update(
            administrator=False,
            embed_links=True,
            attach_files=True,
            external_emojis=default_perms.external_emojis,
            external_stickers=default_perms.external_stickers,
            mention_everyone=default_perms.mention_everyone,
            send_tts_messages=default_perms.send_tts_messages,
        )
        if isinstance(channel, discord.Thread):
            my_perms.send_messages_in_threads = True
        else:
            my_perms.send_messages = True
        return my_perms

    async def send(self, *args, **kwargs):
        ctx = contexts.get()
        if ctx.interaction.is_expired() and ctx._first_response:
            assert ctx.interaction.channel_id
            kwargs["reference"] = discord.MessageReference(
                guild_id=ctx.interaction.guild_id,
                channel_id=ctx.interaction.channel_id,
                message_id=ctx._first_response,
                fail_if_not_exists=False,
            )
            return await ctx.interaction.channel.send(*args, **kwargs)  # type: ignore
        await self.typing()
        ctx._deferring = False
        interaction = ctx.interaction
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
