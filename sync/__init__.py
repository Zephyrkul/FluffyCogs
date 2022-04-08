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

import enum
from posixpath import normpath
from typing import List, Literal, Optional, Set, Union
from typing_extensions import reveal_type

import discord
from redbot.core import commands
from redbot.core.bot import Red


# The below is adapted from AbstractUmbra's sync command, with modifications.
# Original source may be found here: https://github.com/AbstractUmbra/Kukiko/blob/0a96ee4/cogs/admin.py#L185
@commands.is_owner()
@commands.command()
async def sync(ctx: commands.Context, *guilds: Union[Literal["~"], discord.Guild]):
    """
    Sync the bot with the specified guild(s), or globally if no guilds are provided.

    You can provide `~` as shorthand for the current guild.
    `~` will sync globally if this command is invoked in direct messages.

    **Note that global commands can take up to one hour to propagate to the bot's guilds.**
    """
    if not guilds:
        num = len(await ctx.bot.tree.sync(guild=None))
        fmt = "1 command" if num == 1 else f"{num} commands"
        await ctx.send(f"Synced {fmt} globally.")
        return
    seen: Set[Optional[discord.Guild]] = set()
    seen_add = seen.add
    results: List[str] = []
    results_append = results.append
    for guild in guilds:
        if guild == "~":
            guild = ctx.guild
        if guild in seen:
            continue
        seen_add(guild)
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
