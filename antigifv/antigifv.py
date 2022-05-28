import asyncio

import discord
from redbot.core import commands
from redbot.core.bot import Red


class AntiGifV(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        asyncio.ensure_future(self.initialize())

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass

    async def red_get_data_for_user(self, *, user_id):
        return {}

    async def initialize(self):
        # this is probably very dumb
        await self.bot._disabled_cog_cache.default_disable(self.qualified_name)

    @commands.Cog.listener()
    @commands.Cog.listener("on_message_edit")
    async def on_message(self, *args):
        message = args[-1]
        assert isinstance(message, discord.Message)
        if not message.guild:
            return
        assert isinstance(message.channel, discord.TextChannel)
        if not message.channel.permissions_for(message.guild.me).manage_messages:
            return
        if (message.author.id, self.bot.user.id) != (215640856839979008, 256505473807679488) and (
            await self.bot.cog_disabled_in_guild(self, message.guild)
            or await self.bot.is_automod_immune(message)
        ):
            return
        for embed in message.embeds:
            if embed.type == "gifv":
                await message.edit(suppress=True)
                break
