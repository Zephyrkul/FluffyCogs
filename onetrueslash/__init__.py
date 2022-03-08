import asyncio
import logging

try:
    import regex as re
except ImportError:
    import re

from discord import app_commands
from redbot.core import commands as red_commands
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError

from .commands import onetrueslash

LOG = logging.getLogger("red.fluffy.onetrueslash")


async def before_hook(ctx: red_commands.Context):
    if hasattr(ctx, "interaction"):
        await ctx.trigger_typing()


def setup(bot: Red) -> None:
    try:
        if not hasattr(bot, "tree"):
            bot.tree = app_commands.CommandTree(bot)
    except AttributeError:
        raise CogLoadError("This cog requires the latest discord.py 2.0.0a.") from None
    bot.before_invoke(before_hook)
    asyncio.create_task(_setup(bot))


async def _setup(bot: Red):
    assert isinstance(bot.tree, app_commands.CommandTree)
    await bot.wait_until_red_ready()
    assert bot.user
    onetrueslash.name = re.sub(r"[^\w-]+", "_", bot.user.name.casefold())
    bot.tree.add_command(onetrueslash, guild=None)
    await bot.tree.sync(guild=None)


def teardown(bot: Red):
    bot.remove_before_invoke_hook(before_hook)
    if bot.user:
        assert isinstance(bot.tree, app_commands.CommandTree)
        bot.tree.remove_command(onetrueslash.name, guild=None)
        # delay the slash removal a bit in case this is a reload
        asyncio.get_event_loop().call_later(2, asyncio.create_task, bot.tree.sync(guild=None))
