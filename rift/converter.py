import asyncio
import logging
import operator
from typing import List

import discord
from redbot.core import commands
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import inline, pagify
from redbot.core.utils.predicates import MessagePredicate

log = logging.getLogger("red.fluffy.rift.converter")
_ = Translator(__name__, __file__)


class NoRiftsFound(Exception):
    def __init__(self, reasons: "dict[str, str]") -> None:
        self.reasons = reasons
        super().__init__(reasons)


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
        return f"{self.author.global_name}, in {self.channel}"

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}(author={self.author!r}, channel={self.channel!r})"

    _get_channel = property(operator.attrgetter("channel._get_channel"))  # type: ignore


class DiscordConverter(commands.Converter):
    @classmethod
    async def convert(
        cls, ctx: commands.Context, argument: str, *, globally: bool = True
    ) -> discord.abc.Messageable:
        try:
            results = await cls._search(ctx, argument, globally=globally)
        except NoRiftsFound as nrf:
            for page in pagify(
                "No destinations found.\n\n"
                + "\n".join(
                    f"{result} > {reason}".lstrip() for result, reason in nrf.reasons.items()
                )
            ):
                await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())
            raise commands.CheckFailure() from None
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
        except asyncio.TimeoutError:
            m = _("No destination selected.")
            await ctx.send(m)
            raise commands.BadArgument(m) from None
        result = predicate.result
        try:
            return results[result]
        except IndexError:
            raise commands.BadArgument(f"{result} is not a number in the list.") from None

    @classmethod
    async def _search(
        cls, ctx: commands.Context, argument: str, *, globally: bool = False
    ) -> List[discord.abc.Messageable]:
        is_owner = await ctx.bot.is_owner(ctx.author)
        is_nsfw = getattr(ctx.channel, "nsfw", False)
        globally = globally or is_owner
        if not globally and not ctx.guild:
            raise NoRiftsFound({})
        source = ctx.channel if ctx.guild else ctx.author
        config = ctx.cog.config
        blacklists = await asyncio.gather(
            config.all_guilds(), config.all_channels(), config.all_users()
        )
        guilds = ctx.bot.guilds if globally else [ctx.guild]
        results: set[discord.abc.Messageable] = set()
        reasons: dict[str, str] = {}
        for guild in guilds:
            assert guild is not None
            if blacklists[0].get(guild.id, {}).get("blacklisted"):
                continue
            for channel in guild.text_channels:
                if argument.lstrip("#") not in (str(channel.id), channel.mention, channel.name):
                    continue
                if channel in results:
                    continue
                if channel == source:
                    reasons[
                        channel.mention
                    ] = "Rifts cannot be opened to the same channel as their source."
                    continue
                if getattr(channel, "nsfw", False) != is_nsfw:
                    reasons[
                        channel.mention
                    ] = f"Channel {'is not' if is_nsfw else 'is'} nsfw, while this channel {'is' if is_nsfw else 'is not'}."
                    continue
                if blacklists[1].get(channel.id, {}).get("blacklisted"):
                    reasons[channel.mention] = "Channel is blocked from receiving rifts."
                    continue
                results.add(channel)
            if is_nsfw:
                # don't allow rifts from nsfw channels to DMs
                continue
            for user in guild.members:
                if user in results:
                    continue
                to_match = [
                    str(user.id),
                    f"<@{user.id}>",
                    f"<@!{user.id}>",
                    user.name,
                    user.global_name,
                ]
                if guild == ctx.guild:
                    to_match.append(user.display_name)
                if argument.lstrip("@") not in to_match:
                    continue
                if user == source:
                    reasons[user.name] = "Rifts cannot be opened to the same user as their source."
                    continue
                if user.bot:
                    reasons[user.name] = "User is a bot."
                    continue
                if blacklists[2].get(user.id, {}).get("blacklisted"):
                    reasons[user.name] = "User has blocked rifts to their direct messages."
                    continue
                if not await ctx.bot.allowed_by_whitelist_blacklist(user):
                    reasons[user.name] = "User is not permitted to use this bot."
                    continue
                results.add(user._user)
            await asyncio.sleep(0)
        results.discard(None)  # type: ignore
        if results:
            return list(results)
        if reasons:
            raise NoRiftsFound(reasons)
        if is_nsfw:
            raise NoRiftsFound(
                {
                    "": f"If {inline(argument)} is a user, note that rifts cannot be opened to direct messages from nsfw channels."
                }
            )
        raise NoRiftsFound(
            {
                "": f"Either {inline(argument)} does not exist or it is in a server that is blocked from receiving rifts."
            }
        )
