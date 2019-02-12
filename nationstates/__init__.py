from .nationstates import NationStates


async def setup(bot):
    try:
        import sans
    except ImportError as e:
        raise RuntimeError(
            "The sans library is not installed.\n"
            "Run this command to install it: [p]pipinstall sans"
        ) from e
    else:
        if sans.version_info < type(sans.version_info)("0.0.1a3"):
            raise RuntimeError(
                "The sans library is out of date.\n"
                "Run this command to update it: [p]pipinstall sans"
            )
    cog = NationStates(bot)
    await cog.initialize()
    bot.add_cog(cog)
