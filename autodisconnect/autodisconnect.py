import asyncio
from typing import Dict, Union

import discord
from redbot.core import Config, bot, commands


class AutoDisconnect(commands.Cog):
    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    def __init__(self, bot: bot.Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_guild(timeout=-1)
        self.timeout: Dict[int, int] = {}

    @commands.command(aliases=["autodisconnect"])
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def afkdisconnect(self, ctx: commands.Context, *, time: Union[int, bool]):
        """
        Sets how long to wait before disconnecting an AFK member, in seconds.

        Set to -1 to disable.
        """
        if isinstance(time, bool):
            time = 0 if time else -1
        if time < -1:
            raise commands.UserFeedbackCheckFailure(
                "Time must be 0 or greater, or -1 to disable the feature"
            )
        self.timeout[ctx.guild.id] = time
        await self.config.guild(ctx.guild).timeout.set(time)
        await ctx.tick()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        def check(m: discord.Member, b: discord.VoiceState, a: discord.VoiceState):
            if m != member:
                return False
            return a.channel != m.guild.afk_channel

        if not after.channel:
            return
        if not member.guild.afk_channel:
            return
        if before.channel == after.channel:
            return
        if after.channel != member.guild.afk_channel:
            return
        if await self.bot.cog_disabled_in_guild(self, member.guild):
            return
        if member.guild.id not in self.timeout:
            self.timeout[member.guild.id] = await self.config.guild(member.guild).timeout()

        timeout = self.timeout[member.guild.id]
        if timeout < 0:
            return
        if timeout > 0:
            try:
                await self.bot.wait_for("voice_state_update", check=check, timeout=timeout)
            except asyncio.TimeoutError:
                pass  # we want this to happen
            else:
                return  # the member moved on their own
        try:
            await member.move_to(None)
        except discord.HTTPException:
            return
