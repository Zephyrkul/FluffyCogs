from typing import Optional

import discord
from redbot.core import commands as red_commands
from redbot.core.bot import Red

from .commands import onetrueslash
from .utils import valid_app_name


async def before_hook(ctx: red_commands.Context):
    interaction: Optional[discord.Interaction] = getattr(ctx, "_interaction", None)
    if not interaction or getattr(ctx.command, "__commands_is_hybrid__", False):
        return
    ctx.interaction = interaction
    if not interaction.response.is_done():
        ctx._deferring = True  # type: ignore
        await interaction.response.defer(ephemeral=False)


async def on_user_update(before: discord.User, after: discord.User):
    bot: Red = after._state._get_client()  # type: ignore # DEP-WARN
    assert bot.user
    if after.id != bot.user.id:
        return
    if before.name == after.name:
        return
    old_name = onetrueslash.name
    try:
        onetrueslash.name = valid_app_name(after.name)
    except ValueError:
        await bot.send_to_owners(
            f"`onetrueslash` was unable to make the name {after.name!r} "
            "into a valid slash command name. The command name was left unchanged."
        )
        return
    bot.tree.remove_command(old_name)
    bot.tree.add_command(onetrueslash, guild=None)
    await bot.send_to_owners(
        "The bot's username has changed. `onetrueslash`'s slash command has been updated to reflect this.\n"
        "**You will need to re-sync the command tree yourself to see this change.**\n"
        "It is recommended not to change the bot's name too often with this cog, as this can potentially "
        "create confusion for users as well as ratelimiting issues for the bot."
    )
