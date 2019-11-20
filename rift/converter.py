import asyncio
import discord
import logging
from collections import deque, namedtuple
from contextlib import suppress
from dataclasses import dataclass, field
from typing import List

from redbot.core import commands
from redbot.core.utils.chat_formatting import pagify, humanize_list


log = logging.getLogger("red.fluffy.rift.converter")


Rift = namedtuple("Rift", ["author", "source", "destination"])
Rift.__str__ = lambda self: f"{self.author}: {self.source} ► {self.destination}"


def search_converter(translator):
    def convert(argument):
        try:
            return [("author", "source", "destination").index(argument.lower())]
        except ValueError:
            raise commands.BadArgument(
                translator("Argument must be one of author, source, or destination.")
            )

    return convert


class RiftConverter(commands.Converter):
    def __init__(self, translator, *, globally=False):
        self.globally = globally
        self.translator = translator

    async def convert(self, ctx, argument):
        _ = self.translator
        destination = await self.search(ctx, argument, self.globally, _)
        if len(destination) > 1:

            def check(message):
                nonlocal destination
                if message.author != ctx.author or message.channel != ctx.channel:
                    return False
                with suppress(Exception):
                    destination = [destination[int(message.content)]]
                    return True
                return False

            message = _("Multiple results found. Choose a destination:")
            message += "\n\n" + "\n".join(
                f"{i}: {d} ({getattr(d, 'guild')})" if hasattr(d, "guild") else f"{i}: {d}"
                for i, d in enumerate(destination)
            )
            for page in pagify(message):
                await ctx.send(page)
            try:
                await ctx.bot.wait_for("message", check=check)
            except asyncio.TimeoutError as e:
                raise commands.BadArgument(_("Never mind, then.")) from e
        result = destination[0]
        destination = ctx.bot.get_channel(result.id) or ctx.bot.get_user(result.id)
        if not destination:
            raise commands.BadArgument(_("I don't have access to {} anymore.").format(result))
        source = ctx.channel if isinstance(ctx.channel, discord.TextChannel) else ctx.author
        rift = Rift(author=ctx.author, source=source, destination=destination)
        if rift in ctx.cog.open_rifts:
            raise commands.BadArgument(_("This rift already exists."))
        return rift

    @classmethod
    async def search(cls, ctx, argument, globally, _):
        is_owner = await ctx.bot.is_owner(ctx.author)
        config = ctx.cog.config
        guilds = ctx.bot.guilds.copy() if is_owner or globally else [ctx.guild]
        result = set()
        for guild in guilds:
            if not await cls.guild_filter(ctx, is_owner, guild):
                continue
            for channel in guild.text_channels.copy():
                if cls.channel_filter(ctx, is_owner, channel, argument):
                    if not await config.channel(channel).blacklisted():
                        result.add(channel)
                    else:
                        log.debug("Channel %s ignored: blacklisted", channel.id)
            for member in guild.members.copy():
                if member not in result and cls.user_filter(ctx, is_owner, member, argument):
                    if not await config.user(member).blacklisted():
                        result.add(member)
                    else:
                        log.debug("User %s ignored: blacklisted", member.id)
        if not result:
            raise commands.BadArgument(
                _(
                    "Destination {!r} not found. Either I don't have access or it has been blacklisted."
                ).format(argument)
            )
        return list(result)

    @staticmethod
    async def guild_filter(ctx, is_owner, guild):
        if await ctx.cog.config.guild(guild).blacklisted():
            log.debug("Guild %s ignored: blacklisted", guild.id)
            return False
        if guild == ctx.guild:
            return True
        return is_owner or guild.get_member(ctx.author.id)

    @staticmethod
    def channel_filter(ctx, is_owner, channel, argument):
        if ctx.channel == channel:
            log.debug("Channel %s ignored: it is the current channel", channel.id)
            return False
        bot = channel.guild.me
        if not channel.permissions_for(bot).send_messages:
            log.debug("Channel %s ignored: the bot doesn't have access", channel.id)
            return False
        if not is_owner:
            member = channel.guild.get_member(ctx.author.id)
            if not channel.permissions_for(member).send_messages:
                log.debug("Channel %s ignored: user %s doesn't have access", channel.id, member.id)
                return False
        if argument == str(channel.id):
            return True
        if argument == f"<#{channel.id}>":
            return True
        if argument == channel.name:
            return True
        return False

    @staticmethod
    def user_filter(ctx, is_owner, member, argument):
        if ctx.author.id == member.id and isinstance(ctx.channel, discord.DMChannel):
            log.debug("User %s ignored: it is the current channel", member.id)
            return False
        if member.bot:
            log.debug("User %s ignored: it is a bot", member.id)
            return False
        if argument == str(member.id):
            return True
        if argument in (f"<@{member.id}>", f"<@!{member.id}>"):
            return True
        if argument == f"{member.name}#{member.discriminator}":
            return True
        if argument == member.name:
            return True
        if ctx.guild == member.guild and argument == member.display_name:
            return True
        return False
