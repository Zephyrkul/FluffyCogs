import asyncio
import discord

from redbot.core import commands, Config

listener = getattr(commands.Cog, "listener", lambda: (lambda y: y))


class AutoDisconnect(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_guild(timeout=-1)

    @commands.command()
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
    async def on_member_update(self, before, after):
        def check(b, a):
            if not a.voice:
                return True
            if a.voice.channel != a.guild.afk_channel:
                return True
            return False

        if not after.voice:
            return
        if not after.guild.afk_channel:
            return
        b_channel = before.voice.channel if before.voice else None
        if b_channel == after.voice.channel:
            return
        time = await self.config.guild(after.guild).timeout()
        if time < 0:
            return
        if time > 0:
            try:
                await self.bot.wait_for("on_member_update", check=check, timeout=time)
            except asyncio.TimeoutError:
                pass  # we want this to happen
            else:
                return  # the member moved on their own
        try:
            await after.move_to(discord.Object(id=None))
        except discord.HTTPException:
            return
