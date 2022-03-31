from typing import Optional
import logging

import discord
from redbot.core import commands as red_commands

LOG = logging.getLogger("red.fluffy.onetrueslash.events")


async def before_hook(ctx: red_commands.Context):
    interaction: Optional[discord.Interaction]
    if (interaction := getattr(ctx, "interaction", None)) and not interaction.response.is_done():
        ctx._deferring = True  # type: ignore
        await interaction.response.defer(ephemeral=False)
