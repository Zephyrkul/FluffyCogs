from .invoice import InVoice


async def setup(bot):
    cog = InVoice()
    await cog.initialize()
    bot.add_cog(cog)
