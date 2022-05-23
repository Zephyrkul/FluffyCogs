import asyncio
import functools
import heapq
import operator
from copy import copy
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, cast

import discord
from discord import app_commands
from rapidfuzz import fuzz
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.commands.help import HelpSettings
from redbot.core.i18n import set_contextual_locale

from .context import InterContext
from .utils import walk_aliases


@app_commands.command()
@app_commands.describe(
    command="The text-based command to run.",
    arguments="The arguments to provide.",
    attachment="Any files to provide to the command.",
)
async def onetrueslash(
    interaction: discord.Interaction,
    command: str,
    arguments: Optional[str] = None,
    attachment: Optional[discord.Attachment] = None,
) -> None:
    """
    The one true slash command.
    """
    assert isinstance(interaction.client, Red)
    set_contextual_locale(str(interaction.guild_locale or interaction.locale))
    ctx = InterContext.from_interaction(interaction, recreate_message=True)
    error = None
    if command == "help":
        await interaction.response.defer(ephemeral=True)
        actual_command: Optional[commands.Command] = None
        if arguments:
            actual_command = interaction.client.get_command(arguments)
            if actual_command and (signature := actual_command.signature):
                actual_command = copy(actual_command)
                actual_command.usage = f"arguments:{signature}"
        await interaction.client.send_help_for(
            ctx, actual_command or interaction.client, from_help_command=True
        )
    else:
        ferror: asyncio.Task[Tuple[InterContext, commands.CommandError]] = asyncio.create_task(
            interaction.client.wait_for("command_error", check=lambda c, _: c is ctx)
        )
        await interaction.client.invoke(ctx)
        if not interaction.response.is_done():
            ctx._deferring = True
            await interaction.response.defer(ephemeral=True)
        if ferror.done():
            error = ferror.exception() or ferror.result()[1]
        ferror.cancel()
    if ctx._deferring and not interaction.is_expired():
        if error is None:
            if ctx._ticked:
                await interaction.followup.send(ctx._ticked, ephemeral=True)
            else:
                await interaction.delete_original_message()
        elif isinstance(error, commands.CommandNotFound):
            await interaction.followup.send(
                f"❌ Command `{command}` was not found.", ephemeral=True
            )
        elif isinstance(error, commands.CheckFailure):
            await interaction.followup.send(
                f"❌ You don't have permission to run `{command}`.", ephemeral=True
            )


@onetrueslash.autocomplete("command")
async def onetrueslash_command_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    assert isinstance(interaction.client, Red)

    if not await interaction.client.allowed_by_whitelist_blacklist(interaction.user):
        return []

    ctx = InterContext.from_interaction(interaction)
    if not await interaction.client.message_eligible_as_command(ctx.message):
        return []

    help_settings = await HelpSettings.from_context(ctx)
    if current:
        extracted = cast(
            List[str],
            await asyncio.get_event_loop().run_in_executor(
                None,
                heapq.nlargest,
                6,
                walk_aliases(interaction.client, show_hidden=help_settings.show_hidden),
                functools.partial(fuzz.QRatio, current),
            ),
        )
    else:
        extracted = ["help"]
    _filter: Callable[[commands.Command], Awaitable[bool]] = operator.methodcaller(
        "can_run" if help_settings.show_hidden else "can_see", ctx
    )
    matches: Dict[commands.Command, str] = {}
    for name in extracted:
        command = interaction.client.get_command(name)
        if not command or command in matches:
            continue
        try:
            if await _filter(command):
                matches[command] = name
        except commands.CommandError:
            pass
    return [app_commands.Choice(name=name, value=name) for name in matches.values()]


@onetrueslash.error
async def onetrueslash_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    assert isinstance(interaction.client, Red)
    error = getattr(error, "original", error)
    await interaction.client.on_command_error(
        InterContext.from_interaction(interaction), commands.CommandInvokeError(error)
    )
