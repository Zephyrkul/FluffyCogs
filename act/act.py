import discord
import inflect
from typing import Union

from redbot.core import commands
from redbot.core.i18n import Translator, cog_i18n


_ = Translator("Act", __name__)


@cog_i18n(_)
class Act:

    __author__ = "Zephyrkul"

    def __init__(self, bot):
        self.bot = bot
        self.engine = inflect.engine()

    @commands.command(hidden=True)
    async def act(self, ctx, *, target: Union[discord.Member, str] = None):
        """
        Acts on the specified user.
        """
        if not target or isinstance(target, str):
            return  # no help text
        action = ctx.invoked_with
        if not self.engine.singular_noun(action):
            action = self.engine.plural_noun(action)
        await ctx.send(f"*{action} {target.mention}*")

    async def on_message(self, message):
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.prefix is None:
            return

        if ctx.valid and ctx.command and ctx.command.enabled:
            if await ctx.command.can_run(ctx):
                return

        ctx.command = self.act
        await self.bot.invoke(ctx)
