from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement_or_raise

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)

from .autodisconnect import AutoDisconnect


async def setup(bot: Red):
    await bot.add_cog(AutoDisconnect(bot))
