from redbot.core.bot import Red
from redbot.core.errors import CogLoadError
from redbot.core.utils.chat_formatting import humanize_list

from .dev import Dev, patch_hooks, reset_hooks


async def setup(bot: Red):
    if not bot._cli_flags.dev:  # type: ignore
        raise CogLoadError("This cog requires the `--dev` CLI flag.")
    core_dev = bot.get_cog("Dev")
    if sessions := getattr(core_dev, "sessions", None):
        s = "s" if len(sessions) > 1 else ""
        is_private = bot._connection._private_channels.__contains__
        raise CogLoadError(
            f"End your REPL session{s} first: "
            + humanize_list(
                ["Private channel" if is_private(id) else f"<#{id}>" for id in sessions]
            )
        )
    await bot.remove_cog("Dev")
    my_dev = Dev(bot)
    my_dev.env_extensions = getattr(core_dev, "env_extensions", {})
    await bot.add_cog(my_dev)
    patch_hooks()


def teardown(bot: Red):
    reset_hooks()
