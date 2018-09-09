import discord
import inflection
import itertools
import re
from typing import Union

from redbot.core import commands
from redbot.core.utils.chat_formatting import italics

from .helpers import *


class Act:

    __author__ = "Zephyrkul"

    def __init__(self, bot):
        self.bot = bot

    @commands.command(hidden=True)
    async def act(self, ctx, *, target: Union[discord.Member, str] = None):
        """
        Acts on the specified user.
        """
        if not target or isinstance(target, str):
            return  # no help text

        action = inflection.humanize(ctx.invoked_with).split()
        iverbs = []

        for cycle in range(2):
            if iverbs:
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
                iverbs.append(i)

        if not iverbs:
            return
        i = max(iverbs)
        action.insert(i + 1, target.mention)
        await ctx.send(italics(" ".join(action)))

    async def on_message(self, message):
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.prefix is None or not re.match(r"^\w+$", ctx.invoked_with):
            return

        if ctx.valid and ctx.command.enabled:
            if await ctx.command.can_run(ctx):
                return

        ctx.command = self.act
        await self.bot.invoke(ctx)
