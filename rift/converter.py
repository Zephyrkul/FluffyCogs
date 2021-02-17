import asyncio
import logging
import re
from itertools import chain, filterfalse
from typing import List

import discord
from redbot.core import commands
from redbot.core.i18n import Translator
from redbot.core.utils.predicates import MessagePredicate
from urlnorm import norm_netloc

from .irc import IRCMessageable, RiftIRCClient

log = logging.getLogger("red.fluffy.rift.converter")
_ = Translator(__name__, __file__)
user_delim = "~&@%+"
channel_delim = "&#+!"
irc_re = re.compile(
    fr"(?P<users>.+[{user_delim}])?\s*(?P<domain>[^\s{user_delim}{channel_delim}]+)\s*(?P<channels>[{channel_delim}].+)?"
)
user_search = re.compile(fr"[^,\s{user_delim}]+")
channel_search = re.compile(fr"([{channel_delim}]?)([^,\s{channel_delim}]+)")


def URL(argument):
    arg = (None, *argument.split("//"))
    return norm_netloc(*arg[-2:])


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


class IRCConverter(commands.Converter):
    # User1, User2 @ Domain # Channel1, #?Channel2
    @classmethod
    async def convert(cls, ctx, argument):
        argument = await commands.clean_content(fix_channel_mentions=True).convert(ctx, argument)
        parsed = irc_re.fullmatch(argument)
        if not parsed or not parsed.group("domain"):
            raise commands.BadArgument(_("Unknown syntax for IRC destination."))
        if not any((parsed.group("users"), parsed.group("channels"))):
            raise commands.BadArgument(_("No destinations specified."))

        domain = URL(parsed.group("domain"))
        users = user_search.findall(parsed.group("users") or "")
        channels = [
            "".join((t.group(1) or "#", *t.groups()[1:]))
            for t in channel_search.finditer(parsed.group("channels") or "")
        ]
        if "." not in domain and domain != "localhost":
            raise commands.BadArgument(_("Invalid domain."))
        if len(channels) == 1 and re.fullmatch(r"#\d{4}", channels[0]):
            raise commands.BadArgument(_("This is probably a user."))
        if not users and not channels:
            raise commands.BadArgument(_("No destinations specified."))
        client = ctx.cog.irc_clients.get(domain)
        if not client:
            log.debug("No client for domain %s found, creating...", domain)
            client = RiftIRCClient(bot=ctx.bot)
            ctx.cog.irc_clients[domain] = client
            async with ctx.typing():
                # for some reason it sends the connect call and does nothing about it
                await asyncio.gather(
                    ctx.bot.wait_for("pydle_connect", timeout=60), client.connect(domain, tls=True)
                )
                assert client.connected
        else:
            log.debug("Using existing client for domain %s", domain)

        needs_connect = list(filterfalse(client.in_channel, channels))
        await asyncio.gather(
            *(
                ctx.bot.wait_for(
                    "pydle_join", check=lambda cl, ch, us: cl.in_channel(c), timeout=60
                )
                for c in needs_connect
            ),
            *(client.join(c) for c in needs_connect),
        )
        return list(map(client.__getitem__, chain(users, channels)))
