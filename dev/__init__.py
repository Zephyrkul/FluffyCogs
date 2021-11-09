from redbot.core import dev_commands
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError
from redbot.core.utils.chat_formatting import humanize_list

from .dev import Dev, patch_hooks, reset_hooks


def setup(bot: Red):
    if not bot._cli_flags.dev:
        raise CogLoadError("This cog requires the `--dev` CLI flag.")
    if sessions := getattr(bot.get_cog("Dev"), "sessions", None):
        s = "s" if len(sessions) > 1 else ""
        is_private = bot._connection._private_channels.__contains__
        raise CogLoadError(
            f"End your REPL session{s} first: "
            + humanize_list(
                ["Private channel" if is_private(id) else f"<#{id}>" for id in sessions]
            )
        )
    bot.remove_cog("Dev")
    bot.add_cog(Dev())
    patch_hooks()


def teardown(bot: Red):
    reset_hooks()
    bot.remove_cog("Dev")
    bot.add_cog(dev_commands.Dev())
