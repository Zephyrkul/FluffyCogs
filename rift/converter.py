import asyncio
import discord
import logging
from collections import deque, namedtuple
from contextlib import suppress
from dataclasses import dataclass, field
from typing import NewType, List, TYPE_CHECKING
from urlnorm import norm_netloc

from redbot.core import commands
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.chat_formatting import pagify, humanize_list
from redbot.core.i18n import Translator


log = logging.getLogger("red.fluffy.rift.converter")
_ = Translator(__name__, __file__)


def search_converter(argument):
    try:
        return [("author", "source", "destination").index(argument.lower())]
    except ValueError:
        raise commands.BadArgument(_("Argument must be one of author, source, or destination."))


if TYPE_CHECKING:
    URL = str
else:

    def URL(argument):
        arg = (None, *argument.split("//"))
        return norm_netloc(*arg[-2:])


class VertexConverter(commands.Converter):
    @classmethod
    async def convert(
        cls, ctx, argument: str, *, globally: bool = True
    ) -> discord.abc.Messageable:
        results = await cls.search(ctx, argument, globally=globally)
        if len(results) == 0:
            raise commands.BadArgument(_("No destinations found."))
        if len(results) == 1:
            return results[0]
        message = _("Multiple results found. Choose a destination:\n\n")
        message += "\n".join(f"{i}: {result}" for i, result in enumerate(results))
        await ctx.send(message)
        predicate = MessagePredicate.less(len(results), ctx=ctx)
        try:
            await ctx.bot.wait_for("message", check=predicate, timeout=60)
        except asyncio.TimeoutError as te:
            raise commands.BadArgument(_("No destinations selected.")) from te
        return results[predicate]

    @classmethod
    async def search(
        cls, ctx, argument: str, *, globally: bool = False
    ) -> List[discord.abc.Messageable]:
        is_owner = await ctx.bot.is_owner(ctx.author)
        globally = globally or is_owner
        if not globally and not ctx.guild:
            return []
        source = ctx.channel if ctx.guild else ctx.author
        config = ctx.cog.config
        blacklists = await asyncio.gather(
            config.all_guilds(), config.all_channels(), config.all_users()
        )
        if globally:
            guilds = ctx.bot.guilds
        else:
            guilds = [ctx.guild]
        results = []
        for guild in guilds:
            if blacklists[0].get(guild.id, {}).get("blacklisted"):
                continue
            for channel in guild.text_channels:
                if channel == source:
                    continue
                if blacklists[1].get(channel.id, {}).get("blacklisted"):
                    continue
                if argument.lstrip("#") in (str(channel.id), channel.mention, channel.name):
                    results.append(channel)
            for user in guild.members:
                if user == source:
                    continue
                if user.bot:
                    continue
                if blacklists[2].get(user.id, {}).get("blacklisted"):
                    continue
                if argument.lstrip("@") in (
                    str(user.id),
                    f"<@{user.id}>",
                    f"<@!{user.id}>",
                    str(user),
                    user.name,
                ):
                    results.append(user)
                if guild == ctx.guild and argument.lstrip("@") == user.display_name:
                    results.append(user)
        return results
