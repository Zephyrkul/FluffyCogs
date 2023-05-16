from packaging import version
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError
from redbot.core.utils import get_end_user_data_statement_or_raise

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)

try:
    import sans

    from .nationstates import NationStates

    import_failed = None
except ImportError as e:
    import_failed = e


async def setup(bot: Red):
    if import_failed or version.parse(sans.__version__) < version.parse("1.0.0b2"):
        raise CogLoadError(
            "The sans library is out of date or not installed.\n"
            "Run this command to update it: [p]pipinstall sans\n"
            "You may have to [p]restart your bot to have the new version take effect."
        ) from import_failed
    await bot.add_cog(NationStates(bot))
