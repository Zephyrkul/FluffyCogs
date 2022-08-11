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

import itertools
from typing import List, Literal, Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red


# The below is adapted from AbstractUmbra's sync command, with modifications.
# Original source may be found here: https://github.com/AbstractUmbra/Kukiko/blob/fa10b81/extensions/admin.py#L160
@commands.is_owner()
@commands.command()
async def sync(
    ctx: commands.Context,
    guilds: commands.Greedy[discord.Guild],
    spec: Optional[Literal["~", "*", "^"]] = None,
):
    """
    Sync the bot with the specified guild(s), or globally if no guilds are provided.

    Special shorthand symbols can be used as per below. Note that "current guild"
    will mean global commands if this command is used in direct messages.

    Passing `~` will count as shorthand for the current guild.
    Passing `*` will copy the global tree to the current guild's tree and sync to this guild.
    Passing `^` will clear this guild's tree and sync, removing all app commands from this guild.

    **Note that global commands can take up to one hour to propagate to the bot's guilds.**
    """
    if not guilds:
        if spec == "^":
            ctx.bot.tree.clear_commands(guild=None)
        num = len(await ctx.bot.tree.sync(guild=None))
        fmt = "1 command" if num == 1 else f"{num} commands"
        await ctx.send(f"Synced {fmt} globally.")
        return
    results: List[str] = []
    results_append = results.append
    if spec:
        if ctx.guild and spec == "*":
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
        elif spec == "^":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
        guilds.append(ctx.guild)  # type: ignore
    for guild in guilds:
        num = len(await ctx.bot.tree.sync(guild=guild))
        fmt = "1 command" if num == 1 else f"{num} commands"
        scope = f"in {guild.name}" if guild else "globally"
        results_append(f"Synced {fmt} {scope}.")
    await ctx.send("\n".join(results))


async def setup(bot: Red):
    bot.add_command(sync)


async def teardown(bot: Red):
    cmd = bot.remove_command("sync")
    if cmd and cmd is not sync:
        bot.add_command(cmd)
