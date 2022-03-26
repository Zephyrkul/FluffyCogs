import asyncio
import logging
from typing import Optional

try:
    import regex as re
except ImportError:
    import re

import discord
from redbot.core import commands as red_commands
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError

try:
    from discord import app_commands
except ImportError:
    raise CogLoadError("This cog requires the latest discord.py 2.0.0a.") from None

from .commands import onetrueslash

LOG = logging.getLogger("red.fluffy.onetrueslash")


async def before_hook(ctx: red_commands.Context):
    interaction: Optional[discord.Interaction]
    if (interaction := getattr(ctx, "interaction", None)) and not interaction.response.is_done():
        ctx._deferring = True  # type: ignore
        await interaction.response.defer(ephemeral=False)


async def setup(bot: Red) -> None:
    bot.before_invoke(before_hook)
    bot.add_dev_env_value("interaction", lambda ctx: getattr(ctx, "interaction", None))
    asyncio.create_task(_setup(bot))


async def _setup(bot: Red):
    await bot.wait_until_red_ready()
    assert bot.user
    onetrueslash.name = re.sub(r"[^\w-]+", "_", bot.user.name.casefold())
    bot.tree.add_command(onetrueslash, guild=None)
    await bot.tree.sync(guild=None)


async def teardown(bot: Red):
    bot.remove_before_invoke_hook(before_hook)
    if bot.user:
        bot.tree.remove_command(onetrueslash.name, guild=None)
        # delay the slash removal a bit in case this is a reload
        asyncio.create_task(_teardown(bot))
    bot.remove_dev_env_value("interaction")


async def _teardown(bot: Red):
    assert isinstance(bot.tree, app_commands.CommandTree)
    await asyncio.sleep(2)
    await bot.tree.sync(guild=None)
