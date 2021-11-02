import asyncio
import logging
from typing import List

import discord
from redbot.core import commands
from redbot.core.i18n import Translator
from redbot.core.utils.predicates import MessagePredicate

log = logging.getLogger("red.fluffy.rift.converter")
_ = Translator(__name__, __file__)


class Limited(discord.abc.Messageable):
    __slots__ = ("author", "channel")

    def __init__(self, **kwargs):
        super().__init__()
        if message := kwargs.pop("message", None):
            self.author, self.channel = message.author, message.channel
        else:
            self.author, self.channel = kwargs.pop("author"), kwargs.pop("channel")
        if kwargs:
            log.warning(f"Extraneous kwargs for class {self.__class__.__qualname__}: {kwargs}")

    def __getattr__(self, attr):
        return getattr(self.channel, attr)

    def __hash__(self) -> int:
        return hash((self.author, self.channel))

    def __eq__(self, o: object) -> bool:
        if isinstance(o, discord.abc.User):
            return self.author == o or self.channel == o
        if isinstance(o, (discord.TextChannel, discord.DMChannel)):
            return self.channel == o
        try:
            return (self.author, self.channel) == (o.author, o.channel)  # type: ignore
        except AttributeError:
            return NotImplemented

    def __str__(self) -> str:
        return f"{self.author}, in {self.channel}"

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}(author={self.author!r}, channel={self.channel!r})"

    def _get_channel(self):
        return self.channel._get_channel()


class DiscordConverter(commands.Converter):
    @classmethod
    async def convert(
        cls, ctx: commands.Context, argument: str, *, globally: bool = True
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
        predicate = MessagePredicate.valid_int(ctx=ctx)
        try:
            await ctx.bot.wait_for("message", check=predicate, timeout=30)
        except asyncio.TimeoutError as te:
            m = _("No destination selected.")
            await ctx.send(m)
            raise commands.BadArgument(m)
        result = predicate.result
        try:
            return results[result]
        except IndexError:
            raise commands.BadArgument(f"{result} is not a number in the list.") from None

    @classmethod
    async def search(
        cls, ctx: commands.Context, argument: str, *, globally: bool = False
    ) -> List[discord.abc.Messageable]:
        is_owner = await ctx.bot.is_owner(ctx.author)
        is_nsfw = getattr(ctx.channel, "nsfw", False)
        globally = globally or is_owner
        if not globally and not ctx.guild:
            return []
        source = ctx.channel if ctx.guild else ctx.author
        config = ctx.cog.config
        blacklists = await asyncio.gather(
            config.all_guilds(), config.all_channels(), config.all_users()
        )
        guilds = ctx.bot.guilds if globally else [ctx.guild]
        results = set()
        for guild in guilds:
            if blacklists[0].get(guild.id, {}).get("blacklisted"):
                continue
            for channel in guild.text_channels:
                if channel == source:
                    continue
                if getattr(channel, "nsfw", False) != is_nsfw:
                    continue
                if channel in results:
                    continue
                if blacklists[1].get(channel.id, {}).get("blacklisted"):
                    continue
                if argument.lstrip("#") in (str(channel.id), channel.mention, channel.name):
                    results.add(channel)
            if is_nsfw:
                # don't allow rifts from nsfw channels to DMs
                continue
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
