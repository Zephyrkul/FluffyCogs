import discord
from redbot.core.errors import CogLoadError
from .heartattack import HeartAttack


def setup(bot):
    if discord.version_info > (1, 4):
        raise CogLoadError("Heartattack is no longer necessary in d.py 1.4+")
    bot.add_cog(HeartAttack())
