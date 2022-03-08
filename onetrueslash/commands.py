import asyncio
import functools
import operator
from typing import Dict, List, Optional, Tuple, cast

import discord
from discord import app_commands
from fuzzywuzzy import fuzz, process
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.commands.help import HelpSettings

from .context import InterContext
from .utils import walk_with_aliases


@app_commands.command()
@app_commands.describe(
    command="The text-based command to run.", arguments="The arguments to provide."
)
async def onetrueslash(
    interaction: discord.Interaction, command: str, arguments: Optional[str] = None
):
    """
    The one true slash command.
    """
    assert isinstance(interaction.client, Red)
    ctx = await InterContext.from_interaction(interaction, recreate_message=True)
    await interaction.client.invoke(ctx)
    await asyncio.sleep(2)
    if not ctx._deferred:
        if not ctx.command:
            await ctx.send(f"❌ Command `{command}` was not found.", ephemeral=True)
        elif ctx.command_failed:
            await ctx.send(
                "❌ Command failed. Do you have the required permissions?", ephemeral=True
            )
        else:
            await ctx.send("✅ Done.", ephemeral=True)


@onetrueslash.autocomplete("command")
async def onetrueslash_command_autocomplete(
    interaction: discord.Interaction, current: str, namespace: app_commands.Namespace
) -> List[app_commands.Choice[str]]:
    if not current:
        return [app_commands.Choice(name="help", value="help")]

    assert isinstance(interaction.client, Red)
    ctx = await InterContext.from_interaction(interaction)
    helpsettings = await HelpSettings.from_context(ctx)

    extracted = cast(
        List[Tuple[Tuple[str, commands.Command], int]],
        await asyncio.get_event_loop().run_in_executor(
            None,
            functools.partial(
                process.extract,
                (current,),
                walk_with_aliases(interaction.client, show_hidden=helpsettings.show_hidden),
                limit=5,
                processor=operator.itemgetter(0),  # type: ignore - this typehint is incorrect
                scorer=fuzz.QRatio,
            ),
        ),
    )
    _filter = commands.Command.can_run if helpsettings.show_hidden else commands.Command.can_see
    matches: Dict[commands.Command, str] = {}
    for (name, command), score in extracted:
        if command not in matches and await _filter(command, ctx):
            matches[command] = name
    return [app_commands.Choice(name=name, value=name) for name in matches.values()]


@onetrueslash.error
async def onetrueslash_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    assert isinstance(interaction.client, Red)
    error = getattr(error, "original", error)
    await interaction.client.on_command_error(
        await InterContext.from_interaction(interaction), commands.CommandInvokeError(error)
    )
