# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://mozilla.org/MPL/2.0/.

# Copyright (c) 2022, MPL Alex Nørgaard

# Alternatively, the contents of this file may be used under the terms
# of the GNU General Public License Version 3.0, as described below:

# This file is free software: you may copy, redistribute and/or modify
# it under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3.0 of the License, or (at your
# option) any later version.

# This file is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
# Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see http://www.gnu.org/licenses/.

# Copyright (c) 2022, MPL and GPL Eryk De Marco

from typing import List, Literal, Optional
from typing_extensions import Annotated

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify

# Thanks for the tag, Soheab_#6240
MOREINFOMESSAGE = """**Sync when you...**
- Added/removed a command
- Changed a command's...
    - name (`name=` kwarg or function name)
    - description (`description=` kwarg or docstring)
- Added/removed an argument
- Changed an argument's...
    - name (rename decorator)
    - description (describe decorator)
    - type (`arg: str` str is the type here)
- Added/modified permissions:
    - `guild_only` decorator or kwarg
    - `default_permissions` decorator or kwarg
    - `nsfw` kwarg
- Converted the global/guild command to a guild/global command"""


class MoreInfo(discord.ui.View):
    # theoretically anyone can click this button and get a response since there's no check
    # but it's just a simple response so I don't care to do so
    @discord.ui.button(
        label="When should I sync?", emoji="\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16}"
    )
    async def more(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(MOREINFOMESSAGE, ephemeral=True)


# The below is adapted from AbstractUmbra's sync command, with modifications.
# Original source may be found here: https://github.com/AbstractUmbra/Kukiko/blob/fa10b81/extensions/admin.py#L160
@commands.is_owner()
@commands.cooldown(2, 60)
@commands.max_concurrency(1)
@commands.command()
async def sync(
    ctx: commands.Context,
    guilds: Annotated[List[Optional[discord.Guild]], commands.Greedy[discord.Guild]],
    spec: Optional[Literal["~", "*", "^"]] = None,
):
    """
    Sync the bot with the specified guild(s), or globally if no guilds are provided.

    Special shorthand symbols can be used as per below. Note that "current guild"
    will mean global commands if this command is used in direct messages.

    Passing `~` will count as shorthand for the current guild.
    Passing `*` will copy the global tree to the current guild's tree and sync to this guild.
    Passing `^` will clear this guild's tree and sync, removing all app commands from this guild.

    Note that global commands can take up to one hour to propagate to the bot's guilds.
    """
    if spec:
        if ctx.guild and spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
        guilds.append(ctx.guild)
    if not guilds:
        guilds.append(None)
    else:
        guilds = list(dict.fromkeys(guilds))
    results: List[str] = []
    results_append = results.append
    async with ctx.typing():
        for guild in guilds:
            num = len(await ctx.bot.tree.sync(guild=guild))
            fmt = "1 command" if num == 1 else f"{num} commands"
            scope = f"in {guild.name}" if guild else "globally"
            results_append(f"Synced {fmt} {scope}.")
    await ctx.send_interactive(pagify("\n".join(results), shorten_by=0))


@sync.error
async def sync_error(ctx: commands.Context, error: commands.CommandError):
    view = None
    if isinstance(error, commands.CommandOnCooldown):
        message = (
            "It seems you are syncing excessively. "
            "Please keep in mind that syncing has a heavy ratelimit on it, "
            "and it is up to you to sync responsibly.\n"
            "Only sync once you have finished managing your cogs, and remember that "
            "syncing after restarting your bot is **unnecessary**."
        )
        if ctx.bot.get_cog("Dev"):
            timeout = 60
            view = MoreInfo(timeout=timeout)
            message += (
                "\n\nIf you are testing slash commands, it is recommended to use the `*` special flag "
                "to sync to the guild instead and use the more forgiving guild sync ratelimit (\\~5/60s). "
                "Once you have finished testing, you can sync globally and then use the `^` flag to clear "
                "your testing commands.\n"
                "`The cooldown has been reset so you can try again.`"
            )
            ctx.command.reset_cooldown(ctx)
        else:
            timeout = max(30, error.retry_after)
        await ctx.send(message, view=view, delete_after=timeout)
    if not view:
        await ctx.bot.on_command_error(ctx, error, unhandled_by_cog=True)  # type: ignore


async def setup(bot: Red):
    bot.add_command(sync)
