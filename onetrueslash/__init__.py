import asyncio
import logging

from redbot.core import app_commands
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError
from redbot.core.utils import get_end_user_data_statement_or_raise

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)

from .commands import onetrueslash
from .events import before_hook, on_user_update
from .utils import valid_app_name

LOG = logging.getLogger("red.fluffy.onetrueslash")


async def setup(bot: Red) -> None:
    bot.before_invoke(before_hook)
    bot.add_listener(on_user_update)
    bot.add_dev_env_value("interaction", lambda ctx: getattr(ctx, "interaction", None))
    asyncio.create_task(_setup(bot))  # noqa: RUF006


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
    except app_commands.CommandAlreadyRegistered:
        raise CogLoadError(
            f"A slash command named {onetrueslash.name} is already registered."
        ) from None
    except app_commands.CommandLimitReached:
        raise CogLoadError(
            f"{bot.user.name} has already reached the maximum of 100 global slash commands."
        ) from None


async def teardown(bot: Red):
    bot.remove_before_invoke_hook(before_hook)
    bot.remove_dev_env_value("interaction")
