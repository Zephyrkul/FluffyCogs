import asyncio

import discord
from redbot.core import Config, commands

listener = getattr(commands.Cog, "listener", lambda: (lambda y: y))


class AutoDisconnect(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_guild(timeout=-1)

    @commands.command(aliases=["autodisconnect"])
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def afkdisconnect(self, ctx, *, time: int):
        """
        Sets how long to wait before disconnecting an AFK member, in seconds.

        Set to -1 to disable.
        """
        await self.config.guild(ctx.guild).timeout.set(time)
        await ctx.tick()

    @listener()
    async def on_voice_state_update(self, member, before, after):
        def check(m, b, a):
            if m != member:
                return False
            if a.channel != m.guild.afk_channel:
                return True
            return False

        if not after.channel:
            return
        if not member.guild.afk_channel:
            return
        if before.channel == after.channel:
            return
        if after.channel != member.guild.afk_channel:
            return
        time = await self.config.guild(member.guild).timeout()
        if time < 0:
            return
        if time > 0:
            try:
                await self.bot.wait_for("voice_state_update", check=check, timeout=time)
            except asyncio.TimeoutError:
                pass  # we want this to happen
            else:
                return  # the member moved on their own
        try:
            await member.move_to(discord.Object(id=None))
        except discord.HTTPException:
            return
