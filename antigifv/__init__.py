from redbot.core.utils import get_end_user_data_statement_or_raise

from .antigifv import AntiGifV

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot):
    await bot.add_cog(AntiGifV(bot))
