import asyncio
import logging

try:
    import regex as re
except ImportError:
    import re

import discord
from discord import app_commands
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError

from .commands import onetrueslash

LOG = logging.getLogger("red.fluffy.onetrueslash")


def setup(bot: Red) -> None:
    try:
        if not hasattr(bot, "tree"):
            bot.tree = app_commands.CommandTree(bot)
    except AttributeError:
        raise CogLoadError("This cog requires the latest discord.py 2.0.0a.") from None
    asyncio.create_task(_setup(bot))


async def _setup(bot: Red):
    assert isinstance(bot.tree, app_commands.CommandTree)
    await bot.wait_until_red_ready()
    assert bot.user
    if bot.user.id == 256505473807679488:
        guild = discord.Object(id=133049272517001216)
    else:
        guild = None
    onetrueslash.name = re.sub(r"[^\w-]+", "_", bot.user.name.casefold())
    bot.tree.add_command(onetrueslash, guild=guild)
    await bot.tree.sync(guild=guild)


def teardown(bot: Red):
    if bot.user:
        assert isinstance(bot.tree, app_commands.CommandTree)
        if bot.user.id == 256505473807679488:
            guild = discord.Object(id=133049272517001216)
        else:
            guild = None
        bot.tree.remove_command(onetrueslash.name, guild=guild)
        asyncio.create_task(bot.tree.sync(guild=guild))
