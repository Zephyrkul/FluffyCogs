from random import randrange

from redbot.core import commands
from redbot.core.data_manager import bundled_data_path


class Skyrim(commands.Cog):
    """
    Says a random line from Skyrim.
    """

    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    @commands.command()
    async def guard(self, ctx):
        """
        Says a random guard line from Skyrim.
        """
        filepath = bundled_data_path(self) / "lines.txt"
        with filepath.open() as file:
            line = next(file)
            for num, readline in enumerate(file):
                if randrange(num + 2):
                    continue
                line = readline
        await ctx.maybe_send_embed(line)

    @commands.command()
    async def nazeem(self, ctx):
        """
        Do you get to the Cloud District very often?

        Oh, what am I saying, of course you don't.
        """
        await ctx.maybe_send_embed(
            "Do you get to the Cloud District very often? Oh, what am I saying, of course you don't."
        )
