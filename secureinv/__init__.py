from .secureinv import SecureInv


def setup(bot):
    bot.add_cog(SecureInv(bot))
