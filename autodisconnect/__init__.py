from .autodisconnect import AutoDisconnect


def setup(bot):
    bot.add_cog(AutoDisconnect(bot))
