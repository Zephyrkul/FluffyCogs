import discord

from redbot.core import commands, Config, checks

listener = getattr(commands.Cog, "listener", lambda: lambda x: x)


class OnEdit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(timeout=5)

    @commands.command()
    @checks.is_owner()
    async def edittime(self, ctx, *, timeout: float):
        """
        Change how long the bot will listen for message edits to invoke as commands.

        Defaults to 5 seconds.
        Set to 0 to disable.
        """
        if timeout < 0:
            timeout = 0
        await self.config.timeout.set(timeout)
        await ctx.tick()

    @listener()
    async def on_message_edit(self, _, message):
        if (message.edited_at - message.created_at).total_seconds() > await self.config.timeout():
            return
        await self.bot.process_commands(message)
