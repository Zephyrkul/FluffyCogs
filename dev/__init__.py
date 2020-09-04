from .dev import Dev
from redbot.core import dev_commands
from redbot.core.errors import CogLoadError
from redbot.core.bot import Red


def setup(bot: Red):
    if not bot._cli_flags.dev:
        raise CogLoadError("This cog requires the `--dev` CLI flag.")
    bot.remove_cog("Dev")
    bot.add_cog(Dev())


def teardown(bot: Red):
    bot.remove_cog("Dev")
    bot.add_cog(dev_commands.Dev())
