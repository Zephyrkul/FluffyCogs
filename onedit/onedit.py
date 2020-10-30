import asyncio

import discord
from redbot.core import Config, checks, commands, i18n


class OnEdit(commands.Cog):
    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(timeout=5)
        self.timeout = None

    async def edit_process_commands(self, message: discord.Message):
        """Same as Red's method (Red.process_commands), but dont dispatch message_without_command."""
        if not message.author.bot:
            ctx = await self.bot.get_context(message)
            await self.bot.invoke(ctx)
            if ctx.valid is False:
                for allowed_name in ("Alias", "CustomCommands"):
                    if listener := getattr(
                        self.bot.get_cog(allowed_name), "on_message_without_command", None
                    ):
                        asyncio.ensure_future(listener(message))

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
        self.timeout = timeout
        await ctx.tick()

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not after.edited_at:
            return
        if before.content == after.content:
            return
        if self.timeout is None:
            self.timeout = await self.config.timeout()
        if (after.edited_at - after.created_at).total_seconds() > self.timeout:
            return
        if method := getattr(i18n, "set_contextual_locales_from_guild"):
            await method(self.bot, after.guild)
        await self.edit_process_commands(after)
