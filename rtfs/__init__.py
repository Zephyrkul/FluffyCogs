from redbot.core.bot import Red

from .rtfs import RTFS


async def setup(bot: Red):
    await bot.add_cog(RTFS())
