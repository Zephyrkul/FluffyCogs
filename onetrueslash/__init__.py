import asyncio
import logging

import discord
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError

from .commands import onetrueslash
from .events import before_hook, on_user_update
from .utils import valid_app_name

LOG = logging.getLogger("red.fluffy.onetrueslash")


async def setup(bot: Red) -> None:
    if not hasattr(discord, "app_commands"):
        raise CogLoadError("This cog requires the latest discord.py 2.0.0a.")
    bot.before_invoke(before_hook)
    bot.add_listener(on_user_update)
    bot.add_dev_env_value("interaction", lambda ctx: getattr(ctx, "interaction", None))
    asyncio.create_task(_setup(bot))


async def _setup(bot: Red):
    await bot.wait_until_red_ready()
    assert bot.user
    try:
        onetrueslash.name = valid_app_name(bot.user.name)
        bot.tree.add_command(onetrueslash, guild=None)
    except ValueError:
        await bot.send_to_owners(
            f"`onetrueslash` was unable to make the name {bot.user.name!r} "
            "into a valid slash command name. The command name was left unchanged."
        )
    except discord.app_commands.CommandAlreadyRegistered:
        raise CogLoadError(
            f"A slash command named {onetrueslash.name} is already registered."
        ) from None
    except discord.app_commands.CommandLimitReached:
        raise CogLoadError(
            f"{bot.user.name} has already reached the maximum of 100 global slash commands."
        ) from None


async def teardown(bot: Red):
    bot.remove_before_invoke_hook(before_hook)
    bot.remove_listener(on_user_update)
    bot.remove_dev_env_value("interaction")
    bot.tree.remove_command(onetrueslash.name, guild=None)
