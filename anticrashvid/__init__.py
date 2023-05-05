# The file bundled with this cog is a base85-encoded list of known hashes that crash discord,
# as pre-computed by trusted sources.

from redbot.core.utils import get_end_user_data_statement_or_raise

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)

from .anticrashvid import AntiCrashVid


async def setup(bot):
    await bot.add_cog(AntiCrashVid(bot))
