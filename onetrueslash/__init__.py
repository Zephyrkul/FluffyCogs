import asyncio
import logging

try:
    import regex as re
except ImportError:
    import re

import discord
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError

from .commands import onetrueslash
from .events import before_hook

LOG = logging.getLogger("red.fluffy.onetrueslash")


async def setup(bot: Red) -> None:
    if not hasattr(discord, "app_commands"):
        raise CogLoadError("This cog requires the latest discord.py 2.0.0a.")
    bot.before_invoke(before_hook)
    bot.add_dev_env_value("interaction", lambda ctx: getattr(ctx, "interaction", None))
    asyncio.create_task(_setup(bot))


async def _setup(bot: Red):
    await bot.wait_until_red_ready()
    assert bot.user
    onetrueslash.name = re.sub(r"[^\w-]+", "_", bot.user.name.casefold())
    try:
        bot.tree.add_command(onetrueslash, guild=None)
    except discord.app_commands.CommandAlreadyRegistered:
        raise CogLoadError(
            f"A slash command named {onetrueslash.name} is already registered."
        ) from None
    except discord.app_commands.MaxCommandsReached:
        raise CogLoadError(
            f"{bot.user.name} has already reached the maximum of 100 global slash commands."
        ) from None
    else:
        await bot.tree.sync(guild=None)


async def teardown(bot: Red):
    bot.remove_before_invoke_hook(before_hook)
    bot.remove_dev_env_value("interaction")

    bot.tree.remove_command(onetrueslash.name, guild=None)
    # delay the slash sync using a task in case of shutdowns
    asyncio.create_task(_teardown(bot))


async def _teardown(bot: Red):
    await asyncio.sleep(2)
    await bot.tree.sync(guild=None)
