import aiohttp
import discord
import inflection
import itertools
import random
from typing import Union

from redbot.core import commands, checks, Config
from redbot.core.utils.chat_formatting import italics

from .helpers import *


Cog = getattr(commands, "Cog", object)


class Act(Cog):

    __author__ = "Zephyrkul"

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(tenorkey=None)

    @commands.command(hidden=True)
    async def act(self, ctx, *, target: Union[discord.Member, str] = None):
        """
        Acts on the specified user.
        """
        if not target or isinstance(target, str):
            return  # no help text

        # humanize action text
        action = inflection.humanize(ctx.invoked_with).split()
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

        # add reaction gif
        if not ctx.channel.permissions_for(ctx.me).embed_links:
            return await ctx.send(message)
        key = await self.config.tenorkey()
        if not key:
            return await ctx.send(message)
        async with aiohttp.request(
            "GET",
            "https://api.tenor.com/v1/search",
            params={
                "q": ctx.invoked_with,
                "key": key,
                "limit": 8,
                "anon_id": ctx.author.id ^ ctx.me.id,
                "media_filter": "minimal",
                "contentfilter": "low",
                "ar_range": "wide",
                "locale": await ctx.bot.db.locale(),
            },
        ) as response:
            if response.status >= 400:
                json = {}
            else:
                json = await response.json()
        if "results" not in json or not json["results"]:
            return await ctx.send(message)
        message += "\n\n"
        message += random.choice(json["results"])["url"]
        await ctx.send(message)

    @commands.group()
    @checks.is_owner()
    async def actset(self, ctx):
        """
        Configure various settings for the act cog.
        """
        pass

    @actset.command()
    @checks.is_owner()
    async def tenorkey(self, ctx, *, key: str):
        """
        Sets a Tenor GIF API key to enable reaction gifs with act commands.

        You can obtain a key from here: https://tenor.com/developer/dashboard
        """
        if not isinstance(ctx.channel, discord.DMChannel):
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
            return await ctx.send(
                "Please use that command in DM. Since users probably saw your key, it is recommended to reset it right now."
            )
        await self.config.tenorkey.set(key)
        await ctx.author.send("Key set.")

    async def on_message(self, message):
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.prefix is None or not ctx.invoked_with.replace("_", "").isalpha():
            return

        if ctx.valid and ctx.command.enabled:
            if await ctx.command.can_run(ctx):
                return

        ctx.command = self.act
        await self.bot.invoke(ctx)
