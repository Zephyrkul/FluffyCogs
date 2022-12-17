import asyncio
import re
from typing import Dict, Final, Optional, TypedDict, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import quote, spoiler

button: Final = "\N{WHITE SQUARE BUTTON}"
content_re: Final = re.compile(r"(?i)^(?:image|video)/")


class Settings(TypedDict):
    enabled: bool


class Spoilerer(commands.Cog):
    def __init__(self, bot: Red):
        super().__init__()
        self.bot: Final = bot
        self.config: Final[Config] = Config.get_conf(
            self, identifier=2_113_674_295, force_registration=True
        )
        self.config.register_guild(**Settings(enabled=False))

    async def initialize(self):
        all_guilds: Dict[int, Settings] = await self.config.all_guilds()
        self.enabled_guilds = {k for k, v in all_guilds.items() if v["enabled"]}

    @commands.group(invoke_without_command=True)
    async def spoiler(self, ctx: commands.Context, *, message: str = None):
        """
        Spoilers the attachments provided with the message for you.

        The optional `[message]` argument will be posted along with the spoilered attachments.
        The message itself will remain as-is, without any spoilering.
        """
        if not any(
            attach.content_type and content_re.match(attach.content_type)
            for attach in ctx.message.attachments
        ):
            return await ctx.send("You didn't attach any images or videos for me to spoil.")
        await self._spoil(ctx.message, message)

    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    @spoiler.command()
    async def message(
        self,
        ctx: commands.GuildContext,
        spoiler_content: Optional[bool],
        *,
        message: discord.Message = None,
    ):
        """
        Spoilers the specified message's attachments by deleting it and re-posting it.

        Pass `True` to `spoiler_content` to also spoiler the message text, e.g.:
        `[p]spoiler message True 1053802538056548392`

        If `message` is not specified, then this will default to the message you reply to
        when using the command.
        """
        if not message:
            if ctx.message.reference:
                message = ctx.message.reference.resolved
            else:
                await ctx.send_help()
                return
        if (
            message.author != ctx.me
            and not message.channel.permissions_for(ctx.me).manage_messages
        ):
            raise commands.BotMissingPermissions(discord.Permissions(manage_messages=True))
        await self._spoil(
            message, spoiler(message.content) if spoiler_content else message.content
        )
        await ctx.tick()

    @commands.admin_or_permissions(manage_messages=True)
    @commands.guild_only()
    @spoiler.command()
    async def button(self, ctx: commands.GuildContext, *, enable: bool):
        """
        Enable or disable the spoiler button for this guild.

        The spoiler button adds \N{WHITE SQUARE BUTTON} as a reaction to any attachments
        sent by members that are on mobile or that are invisible.
        Clicking this button acts as if they used the `[p]spoiler` command.
        """
        guild = ctx.guild
        if enable:
            self.enabled_guilds.add(guild.id)
            await self.config.guild(guild).enabled.set(True)
        else:
            self.enabled_guilds.discard(guild.id)
            await self.config.guild(guild).enabled.set(False)
        await ctx.send(
            f"The {button} spoiler button is {'now' if enable else 'no longer'} enabled"
        )

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        author = message.author
        if author.bot:
            return
        if not message.attachments:
            return
        guild = message.guild
        if guild and guild.id not in self.enabled_guilds:
            return
        if (
            sum(attach.size for attach in message.attachments)
            > getattr(guild, "filesize_limit", 1 << 23) - 10_000
        ):
            return
        if all(attach.is_spoiler() for attach in message.attachments):
            return
        me: Union[discord.ClientUser, discord.Member] = (message.guild or message.channel).me  # type: ignore
        # 0x2040 - add_reactions, manage_messages
        if guild and message.channel.permissions_for(me).value & 0x2040 != 0x2040:  # type: ignore
            return
        for dg in [guild] if guild else filter(None, map(self.bot.get_guild, self.enabled_guilds)):
            if (dm := dg.get_member(author.id)) and not await self.bot.cog_disabled_in_guild(
                self, dg
            ):
                break
        else:
            return
        if dm.status != discord.Status.offline and not dm.is_on_mobile():
            return
        try:
            await message.add_reaction(button)
        except discord.Forbidden:
            return

        def check(r: discord.Reaction, u: Union[discord.Member, discord.User]):
            return r.message == message and r.emoji == button and u == author

        try:
            await self.bot.wait_for("reaction_add", timeout=10, check=check)
        except asyncio.TimeoutError:
            try:
                await message.remove_reaction(button, me)
            except discord.HTTPException:
                return
        else:
            await self._spoil(message)

    @staticmethod
    async def _spoil(message: discord.Message, content: Optional[str] = ...):
        if content is ...:
            content = message.content
        channel = message.channel
        files = await asyncio.gather(
            *(
                attach.to_file(spoiler=True)
                for attach in message.attachments
                if attach.content_type and content_re.match(attach.content_type)
            )
        )
        me: Union[discord.ClientUser, discord.Member]
        if guild := message.guild:
            assert isinstance(channel, discord.TextChannel)
            me = guild.me
            if content:
                content = f"from {message.author.mention}\n{quote(message.content)}"
            else:
                content = f"from {message.author.mention}"
            if channel.permissions_for(me).manage_messages:
                await message.delete(delay=0)
        else:
            assert isinstance(channel, discord.DMChannel)
            me = channel.me
            content = None
        await message.channel.send(
            content, files=files, reference=message.reference, mention_author=False
        )
