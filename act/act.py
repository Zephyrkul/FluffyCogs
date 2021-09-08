import functools
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Match, Optional, Union

import aiohttp
import discord
import inflection
from redbot.core import Config, bot, commands, i18n
from redbot.core.utils.chat_formatting import italics

from .helpers import *

fmt_re = re.compile(r"{(?:0|user)(?:\.([^\{]+))?}")
cmd_re = re.compile(r"[a-zA-Z_]+")


def guild_only_without_subcommand():
    def predicate(ctx: commands.Context):
        if ctx.guild is None and ctx.invoked_subcommand is None:
            raise commands.NoPrivateMessage()
        return True

    return commands.check(predicate)


class Act(commands.Cog):
    """
    This cog makes all commands, e.g. [p]fluff, into valid commands if
    you command the bot to act on a user, e.g. [p]fluff [botname].
    """

    __author__ = "Zephyrkul"

    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    def __init__(self, bot: bot.Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(custom={}, tenorkey=None)
        self.config.register_guild(custom={})
        self.try_after = None

    async def initialize(self, bot: bot.Red):
        # temporary backwards compatibility
        key = await self.config.tenorkey()
        if not key:
            return
        await bot.set_shared_api_tokens("tenor", api_key=key)
        await self.config.tenorkey.clear()

    @staticmethod
    def repl(target: discord.Member, match: Match[str]) -> str:
        if attr := match.group(1):
            if attr.startswith("_") or "." in attr:
                return str(target)
            try:
                return str(getattr(target, attr))
            except AttributeError:
                return str(target)
        return str(target)

    @commands.command(hidden=True)
    async def act(self, ctx: commands.Context, *, target: Union[discord.Member, str] = None):
        if not target or isinstance(target, str):
            return  # no help text

        try:
            if not ctx.guild:
                raise KeyError()
            message = await self.config.guild(ctx.guild).get_raw("custom", ctx.invoked_with)
        except KeyError:
            try:
                message = await self.config.get_raw("custom", ctx.invoked_with)
            except KeyError:
                message = NotImplemented

        humanized: Optional[str] = None
        if message is None:  # ignored command
            return
        elif message is NotImplemented:  # default
            # humanize action text
            humanized = inflection.humanize(ctx.invoked_with)
            action = humanized.split()
            iverb = -1

            for cycle in range(2):
                if iverb > -1:
                    break
                for i, act in enumerate(action):
                    act = act.lower()
                    if (
                        act in NOLY_ADV
                        or act in CONJ
                        or (act.endswith("ly") and act not in LY_VERBS)
                        or (not cycle and act in SOFT_VERBS)
                    ):
                        continue
                    action[i] = inflection.pluralize(action[i])
                    iverb = max(iverb, i)

            if iverb < 0:
                return
            action.insert(iverb + 1, target.mention)
            message = italics(" ".join(action))
        else:
            assert isinstance(message, str)
            message = fmt_re.sub(functools.partial(self.repl, target), message)

        send = functools.partial(
            ctx.send,
            allowed_mentions=discord.AllowedMentions(
                users=False if target in ctx.message.mentions else [target]
            ),
        )

        # add reaction gif
        if self.try_after and ctx.message.created_at < self.try_after:
            return await send(message)
        if not await ctx.embed_requested():
            return await send(message)
        key = (await ctx.bot.get_shared_api_tokens("tenor")).get("api_key")
        if not key:
            return await send(message)
        if humanized is None:
            humanized = inflection.humanize(ctx.invoked_with)
        async with aiohttp.request(
            "GET",
            "https://g.tenor.com/v1/search",
            params={
                "q": humanized,
                "key": key,
                "anon_id": str(ctx.author.id ^ ctx.me.id),
                "media_filter": "minimal",
                "contentfilter": "off" if getattr(ctx.channel, "nsfw", False) else "low",
                "ar_range": "wide",
                "limit": 20,
                "locale": i18n.get_locale(),
            },
        ) as response:
            json: dict
            if response.status == 429:
                self.try_after = ctx.message.created_at + timedelta(seconds=30)
                json = {}
            elif response.status >= 400:
                json = {}
            else:
                json = await response.json()
        if not json.get("results"):
            return await send(message)
        # Try to keep gifs more relevant by only grabbing from the top 50% + 1 of results,
        # in case there are only a few results.
        # math.ceiling() is not used since it would be too limiting for smaller lists.
        choice = json["results"][random.randrange(len(json["results"]) // 2 + 1)]
        choice = random.choice(json["results"])
        embed = discord.Embed(
            color=await ctx.embed_color(),
            timestamp=datetime.fromtimestamp(choice["created"], timezone.utc),
            url=choice["itemurl"],
        )
        # This footer is required by Tenor's API: https://tenor.com/gifapi/documentation#attribution
        embed.set_footer(text="Via Tenor")
        embed.set_image(url=choice["media"][0]["gif"]["url"])
        await send(message, embed=embed)

    # because people keep using [p]help act instead of [p]help Act
    act.callback.__doc__ = __doc__

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def actset(self, ctx: commands.Context):
        """
        Configure various settings for the act cog.
        """

    @actset.group(aliases=["custom", "customise"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @guild_only_without_subcommand()
    async def customize(self, ctx: commands.GuildContext, command: str, *, response: str = None):
        """
        Customize the response to an action.

        You can use {0} or {user} to dynamically replace with the specified target of the action.
        Formats like {0.name} or {0.mention} can also be used.
        """
        if not response:
            await self.config.guild(ctx.guild).clear_raw("custom", command)
            await ctx.tick()
        else:
            await self.config.guild(ctx.guild).set_raw("custom", command, value=response)
            await ctx.send(
                fmt_re.sub(functools.partial(self.repl, ctx.author), response),
                allowed_mentions=discord.AllowedMentions(users=False),
            )

    @customize.command(name="global")
    @commands.is_owner()
    async def customize_global(self, ctx: commands.Context, command: str, *, response: str = None):
        """
        Globally customize the response to an action.

        You can use {0} or {user} to dynamically replace with the specified target of the action.
        Formats like {0.name} or {0.mention} can also be used.
        """
        if not response:
            await self.config.clear_raw("custom", command)
        else:
            await self.config.set_raw("custom", command, value=response)
        await ctx.tick()

    @actset.group(invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @guild_only_without_subcommand()
    async def ignore(self, ctx: commands.GuildContext, command: str):
        """
        Ignore or unignore the specified action.

        The bot will no longer respond to these actions.
        """
        try:
            custom = await self.config.guild(ctx.guild).get_raw("custom", command)
        except KeyError:
            custom = NotImplemented
        if custom is None:
            await self.config.guild(ctx.guild).clear_raw("custom", command)
            await ctx.send("I will no longer ignore the {command} action".format(command=command))
        else:
            await self.config.guild(ctx.guild).set_raw("custom", command, value=None)
            await ctx.send("I will now ignore the {command} action".format(command=command))

    @ignore.command(name="global")
    @commands.is_owner()
    async def ignore_global(self, ctx: commands.Context, command: str):
        """
        Globally ignore or unignore the specified action.

        The bot will no longer respond to these actions.
        """
        try:
            await self.config.get_raw("custom", command)
        except KeyError:
            await self.config.set_raw("custom", command, value=None)
        else:
            await self.config.clear_raw("custom", command)
        await ctx.tick()

    @actset.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def embed(self, ctx: commands.Context):
        """
        Manage tenor embed settings for this cog.
        """
        await ctx.maybe_send_embed(
            "You can enable or disable whether this cog attaches tenor gifs "
            f"by using `{ctx.clean_prefix}embedset command act on/off`."
        )

    @actset.command()
    @commands.is_owner()
    async def tenorkey(self, ctx: commands.Context):
        """
        Sets a Tenor GIF API key to enable reaction gifs with act commands.

        You can obtain a key from here: https://tenor.com/developer/dashboard
        """
        instructions = [
            "Go to the Tenor developer dashboard: https://tenor.com/developer/dashboard",
            "Log in or sign up if you haven't already.",
            "Click `+ Create new app` and fill out the form.",
            "Copy the key from the app you just created.",
            "Give the key to Red with this command:\n"
            f"`{ctx.clean_prefix}set api tenor api_key your_api_key`\n"
            "Replace `your_api_key` with the key you just got.\n"
            "Everything else should be the same.\n\n",
            "You can disable embeds again by using this command:\n"
            f"`{ctx.clean_prefix}embedset command act off`",
        ]
        instructions = [f"**{i}.** {v}" for i, v in enumerate(instructions, 1)]
        await ctx.maybe_send_embed("\n".join(instructions))

    @commands.Cog.listener()
    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError, unhandled_by_cog: bool = False
    ):
        if ctx.command == self.act:
            return
        if not self.act.enabled:
            return
        if not cmd_re.fullmatch(ctx.invoked_with):
            return
        if await ctx.bot.cog_disabled_in_guild(self, ctx.guild):
            return
        if isinstance(error, commands.UserFeedbackCheckFailure):
            # UserFeedbackCheckFailure inherits from CheckFailure
            return
        if not isinstance(error, (commands.CheckFailure, commands.CommandNotFound)):
            return
        ctx.command = self.act
        await ctx.bot.invoke(ctx)
