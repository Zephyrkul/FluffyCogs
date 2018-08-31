from .rift import Rift


def setup(bot):
    bot.add_cog(Rift(bot))
