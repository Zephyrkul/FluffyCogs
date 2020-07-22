import asyncio
import logging
from itertools import chain, filterfalse
from typing import List

import discord
from redbot.core import commands
from redbot.core.i18n import Translator
from redbot.core.utils.predicates import MessagePredicate
from urlnorm import norm_netloc

log = logging.getLogger("red.fluffy.rift.converter")
_ = Translator(__name__, __file__)


class Limited(discord.abc.Messageable):
    def __init__(self, *, message: discord.Message):
        self.message = message

    def __getattr__(self, attr):
        try:
            return getattr(self.message.channel, attr)
        except AttributeError:
            return getattr(self.message, attr)

    def __hash__(self) -> int:
        return hash((self.message.author, self.message.channel))

    def __eq__(self, o: object) -> bool:
        if not isinstance(o, Limited):
            return NotImplemented
        return (self.message.author, self.message.channel) == (o.message.author, o.message.channel)

    def __str__(self) -> str:
        return f"{self.message.author}, in {self.message.channel}"

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}(message={self.message!r})"

    def _get_channel(self):
        return self.message.channel._get_channel()


class DiscordConverter(commands.Converter):
    @classmethod
    async def convert(
        cls, ctx, argument: str, *, globally: bool = True
    ) -> discord.abc.Messageable:
        results = await cls.search(ctx, argument, globally=globally)
        if len(results) == 0:
            m = _("No destinations found.")
            await ctx.send(m)
            raise commands.BadArgument(m)
        if len(results) == 1:
            return results[0]
        message = _("Multiple results found. Choose a destination:\n\n")
        for i, result in enumerate(results):
            m = f"{i}: {result} ({result.id})"
            if guild := getattr(result, "guild", None):
                m = f"{m}, in {guild}"
            message = f"{message}\n{m}"
        await ctx.send(message)
        predicate = MessagePredicate.less(len(results), ctx=ctx)
        try:
            await ctx.bot.wait_for("message", check=predicate, timeout=30)
        except asyncio.TimeoutError as te:
            m = _("No destination selected.")
            await ctx.send(m)
            raise commands.BadArgument(m)
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
        results = set()
        for guild in guilds:
            if blacklists[0].get(guild.id, {}).get("blacklisted"):
                continue
            for channel in guild.text_channels:
                if channel == source:
                    continue
                if channel in results:
                    continue
                if blacklists[1].get(channel.id, {}).get("blacklisted"):
                    continue
                if argument.lstrip("#") in (str(channel.id), channel.mention, channel.name):
                    results.add(channel)
            for user in guild.members:
                if user == source:
                    continue
                if user.bot:
                    continue
                if user in results:
                    continue
                if blacklists[2].get(user.id, {}).get("blacklisted"):
                    continue
                to_match = [str(user.id), f"<@{user.id}>", f"<@!{user.id}>", str(user), user.name]
                if guild == ctx.guild:
                    to_match.append(user.display_name)
                if argument.lstrip("@") not in to_match:
                    continue
                if not await ctx.bot.allowed_by_whitelist_blacklist(user):
                    continue
                results.add(ctx.bot.get_user(user.id))
            await asyncio.sleep(0)
        results.discard(None)
        return list(results)
