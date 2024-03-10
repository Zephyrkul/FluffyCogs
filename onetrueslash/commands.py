import asyncio
import functools
import heapq
import operator
from copy import copy
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, cast

import discord
from rapidfuzz import fuzz
from redbot.core import app_commands, commands
from redbot.core.bot import Red
from redbot.core.commands.help import HelpSettings
from redbot.core.i18n import set_contextual_locale

from .context import InterContext
from .utils import walk_aliases


@app_commands.command(extras={"red_force_enable": True})
async def onetrueslash(
    interaction: discord.Interaction,
    command: str,
    arguments: Optional[str] = None,
    attachment: Optional[discord.Attachment] = None,
) -> None:
    """
    The one true slash command.

    Parameters
    -----------
    command: str
        The text-based command to run.
    arguments: Optional[str]
        The arguments to provide to the command, if any.
    attachment: Optional[Attachment]
        The attached file to provide to the command, if any.
    """
    assert isinstance(interaction.client, Red)
    set_contextual_locale(str(interaction.guild_locale or interaction.locale))
    actual = interaction.client.get_command(command)
    ctx = await InterContext.from_interaction(interaction, recreate_message=True)
    error = None
    if command == "help":
        ctx._deferring = True
        # Moving ctx._interaction can cause check errors with some hybrid commands
        # see https://github.com/Zephyrkul/FluffyCogs/issues/75 for details
        # ctx.interaction = interaction
        await interaction.response.defer(ephemeral=True)
        actual = None
        if arguments:
            actual = interaction.client.get_command(arguments)
            if actual and (signature := actual.signature):
                actual = copy(actual)
                actual.usage = f"arguments:{signature}"
        await interaction.client.send_help_for(
            ctx, actual or interaction.client, from_help_command=True
        )
    else:
        ferror: asyncio.Task[Tuple[InterContext, commands.CommandError]] = asyncio.create_task(
            interaction.client.wait_for("command_error", check=lambda c, _: c is ctx)
        )
        ferror.add_done_callback(lambda _: setattr(ctx, "interaction", interaction))
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
                await interaction.delete_original_response()
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

    ctx = await InterContext.from_interaction(interaction)
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
                functools.partial(fuzz.token_sort_ratio, current),
            ),
        )
        extracted.append("help")
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
            if name == "help" and await command.can_run(ctx) or await _filter(command):
                if len(name) > 100:
                    name = name[:99] + "\N{HORIZONTAL ELLIPSIS}"
                matches[command] = name
        except commands.CommandError:
            pass
    return [app_commands.Choice(name=name, value=name) for name in matches.values()]


@onetrueslash.error
async def onetrueslash_error(interaction: discord.Interaction, error: Exception):
    assert isinstance(interaction.client, Red)
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original
    error = getattr(error, "original", error)
    await interaction.client.on_command_error(
        await InterContext.from_interaction(interaction, recreate_message=True),
        commands.CommandInvokeError(error),
    )
