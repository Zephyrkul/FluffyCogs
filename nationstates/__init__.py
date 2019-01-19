from .nationstates import NationStates


async def setup(bot):
    try:
        import sans
    except ImportError as e:
        raise RuntimeError(
            "The sans library is not installed.\n"
            "Run this command to install it: [p]pipinstall sans"
        ) from e
    cog = NationStates(bot)
    await cog.initialize()
    bot.add_cog(cog)
