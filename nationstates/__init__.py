import warnings

from redbot.core.errors import CogLoadError

warnings.filterwarnings("ignore", module=r"sans.*")

try:
    import sans

    from .nationstates import NationStates

    import_failed = None
except ImportError as e:
    import_failed = e


async def setup(bot):
    if import_failed or sans.version_info < type(sans.version_info)("0.0.1b6"):
        raise CogLoadError(
            "The sans library is out of date or not installed.\n"
            "Run this command to update it: [p]pipinstall sans\n"
            "You may have to [p]restart your bot to have the new version take effect."
        ) from import_failed
    cog = NationStates(bot)
    await cog.initialize()
    bot.add_cog(cog)
