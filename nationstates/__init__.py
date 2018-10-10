from .nationstates import NationStates


async def setup(bot):
    cog = NationStates(bot)
    await cog.initialize()
    bot.add_cog(cog)
