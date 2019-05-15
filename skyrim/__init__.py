from redbot.core import data_manager
from .skyrim import Skyrim


def setup(bot):
    bot.add_cog(Skyrim())
