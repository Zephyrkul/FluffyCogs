import discord
from redbot.core import Config, commands

from .chart import pie


def n_or_greater(n):
    def bounded_int(argument):
        argument = int(argument)
        if argument < n:
            raise ValueError
        return argument

    return bounded_int


def nonzero_int(argument):
    argument = int(argument)
    if argument == 0:
        raise ValueError
    return argument


class Clocks(commands.Cog):

    # TODO: async def red_get_data_for_user(self, *, user_id):

    async def red_delete_data_for_user(self, *, requester, user_id):
        # Nothing here is operational, so just delete it all
        await self.config.user_from_id(user_id).clear()

    def __init__(self):
        super().__init__()
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_user(clocks={})

    @commands.group(aliases=["clock"])
    async def clocks(self, ctx):
        """Track projects with clocks"""

    @clocks.command()
    async def create(
        self, ctx, name: str.lower, slices: n_or_greater(2), *, start: n_or_greater(0) = 0
    ):
        """Create a new clock"""
        async with self.config.user(ctx.author).clocks() as clocks:
            if name in clocks:
                return await ctx.send("This clock already exists.")
            clocks[name] = [start, slices]
        await ctx.send(pie(start, slices))

    @clocks.command()
    async def delete(self, ctx, *, name: str.lower):
        """Delete a clock"""
        async with self.config.user(ctx.author).clocks() as clocks:
            clocks.pop(name, None)
        await ctx.send("Clock deleted.")

    @clocks.command()
    async def extend(self, ctx, name: str.lower, *, slices: nonzero_int):
        """Modify a clock's maximum slices."""
        async with self.config.user(ctx.author).clocks() as clocks:
            try:
                this_clock = clocks[name]
            except KeyError:
                return await ctx.send("No such clock.")
            this_clock[1] = max(2, this_clock[1] + slices)
            this_clock[0] = sorted((0, this_clock[0], this_clock[1]))[1]
        await ctx.send(pie(*this_clock))

    @clocks.command(aliases=["add", "modify"])
    async def mod(self, ctx, name: str.lower, *, slices: nonzero_int):
        """Modify a clock's progress."""
        async with self.config.user(ctx.author).clocks() as clocks:
            try:
                this_clock = clocks[name]
            except KeyError:
                return await ctx.send("No such clock.")
            this_clock[0] += slices
            this_clock[0] = sorted((0, this_clock[0], this_clock[1]))[1]
        await ctx.send(pie(*this_clock))

    @clocks.command(name="set")
    async def _set(
        self, ctx, name: str.lower, slices: n_or_greater(0), *, max: n_or_greater(2) = None
    ):
        """Sets a clock's state."""
        async with self.config.user(ctx.author).clocks() as clocks:
            try:
                this_clock = clocks[name]
            except KeyError:
                return await ctx.send("No such clock.")
            if max:
                this_clock[1] = max
            this_clock[0] = sorted((0, slices, this_clock[1]))[1]
        await ctx.send(pie(*this_clock))

    @clocks.command()
    async def show(self, ctx, name: str.lower = None, *, user: discord.Member = None):
        """Show a clock's progress."""
        if user and not ctx.guild:
            return
        if not user:
            user = ctx.author
        clocks = await self.config.user(user).clocks()
        try:
            result = pie(*clocks[name]) if name else ", ".join(clocks.keys())
        except KeyError:
            return await ctx.send("No such clock.")
        if result:
            return await ctx.send(result)
        await ctx.send("No clocks created.")
