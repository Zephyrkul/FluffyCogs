from .turn import Turn


def setup(bot):
    bot.add_cog(Turn(bot))
