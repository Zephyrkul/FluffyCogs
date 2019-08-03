from .act import Act


async def setup(bot):
    act = Act(bot)
    await act.initialize(bot)
    bot.add_cog(act)
