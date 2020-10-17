import asyncio
import contextlib
import inspect
import logging
import re
import typing
from datetime import timedelta
from functools import partial
from typing import DefaultDict, Dict, Optional, Set, Tuple, TypedDict

import discord
from proxyembed import ProxyEmbed
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils import AsyncIter
from redbot.core.utils.antispam import AntiSpam


class GuildSettings(TypedDict):
    role: Optional[int]
    dynamic: bool
    dynamic_name: Optional[str]
    mute: bool
    deaf: bool
    self_deaf: bool


class ChannelSettings(TypedDict):
    role: Optional[int]
    channel: Optional[int]


GuildCache = DefaultDict[int, GuildSettings]
ChannelCache = DefaultDict[int, ChannelSettings]
LOG = logging.getLogger("red.fluffy.invoice")


class InVoice(commands.Cog):
    intervals = [
        (timedelta(seconds=5), 3),
        (timedelta(minutes=1), 5),
        (timedelta(hours=1), 30),
    ]

    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_guild(**self._guild_defaults())
        self.config.register_channel(**self._channel_defaults())
        self.guild_cache: GuildCache = DefaultDict(self._guild_defaults)
        self.channel_cache: ChannelCache = DefaultDict(self._channel_defaults)
        self.guild_as: DefaultDict[int, AntiSpam] = DefaultDict(partial(AntiSpam, self.intervals))
        self.member_as: DefaultDict[Tuple[int, int], AntiSpam] = DefaultDict(
            partial(AntiSpam, self.intervals)
        )
        self.dynamic_ready: Dict[int, asyncio.Event] = {}
        self.cog_ready = asyncio.Event()
        asyncio.create_task(self.initialize())

    async def cleanup_task(self):
        # Older versions of invoice failed to properly clean up deleted VC settings
        # This will rectify that
        channel_id: int
        settings: ChannelSettings
        async with self.config.get_channels_lock():
            async for channel_id, settings in AsyncIter(
                (await self.config.all_channels()).items(), steps=100
            ):
                if not any(settings.values()):
                    await self.config.channel_from_id(channel_id).clear()

    async def initialize(self):
        await self.cleanup_task()
        self.guild_cache = DefaultDict(self._guild_defaults, await self.config.all_guilds())
        self.channel_cache = DefaultDict(self._channel_defaults, await self.config.all_channels())
        self.cog_ready.set()

    def _guild_defaults(self):
        return GuildSettings(
            role=None, dynamic=False, dynamic_name=None, mute=False, deaf=False, self_deaf=False
        )

    def _channel_defaults(self):
        return ChannelSettings(role=None, channel=None)

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        await self.cog_ready.wait()

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def invoice(self, ctx):
        """
        Configure or view settings for automated voice-based permissions.
        """
        if ctx.invoked_subcommand:
            return
        color = await ctx.embed_colour()

        embed = ProxyEmbed(title=f"Current settings", color=color)
        g_settings = self.guild_cache[ctx.guild.id]
        g_msg = []
        for key, value in g_settings.items():
            if value is not None and key == "role":
                value = ctx.guild.get_role(value)
                value = value.name if value else "<Deleted role>"
            key = key.replace("_", " ").title()
            g_msg.append(f"{key}: {value}")
        embed.add_field(name=f"Guild {ctx.guild} settings", value="\n".join(g_msg))

        vc = ctx.author.voice.channel if ctx.author.voice else None
        if vc:
            c_settings = self.channel_cache[ctx.channel.id]
            c_msg = []
            for key, value in c_settings.items():
                if value is not None:
                    if key == "role":
                        value = ctx.guild.get_role(value)
                        value = value.name if value else "<Deleted role>"
                    elif key == "channel":
                        value = ctx.guild.get_channel(value)
                        value = value.name if value else "<Deleted channel>"
                key = key.replace("_", " ").title()
                c_msg.append(f"{key}: {value}")
            embed.add_field(name=f"Channel {vc} settings", value="\n".join(c_msg))
        await embed.send_to(ctx)

    @invoice.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def dynamic(self, ctx: commands.Context, *, true_or_false: bool = None):
        """
        Toggle whether to dynamically create a role and channel for new voice channels when they're created.

        The new role will inherit the permissions of the guild-wide role, if there is one,
        and the text channel will inherit the permissions of the category the new channel is in.
        """
        if true_or_false is None:
            true_or_false = not self.guild_cache[ctx.guild.id]["dynamic"]
        await self.config.guild(ctx.guild).dynamic.set(true_or_false)
        self.guild_cache[ctx.guild.id]["dynamic"] = true_or_false
        await ctx.send(
            "I will {} dynamically create roles and channels for new VCs.".format(
                "now" if true_or_false else "no longer"
            )
        )

    @dynamic.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def name(self, ctx: commands.Context, *, new_name: str = None):
        """
        Set the name of the dynamic role and text channel when they're generated.

        You can use `{vc}` as a placeholder, which will be replaced by the voice channel's name.
        Defaults to `\N{SPEAKER WITH THREE SOUND WAVES} {vc}`
        """
        if not new_name:
            async with self.config.guild(ctx.guild).all() as guild_settings:
                old_name = guild_settings.pop("dynamic_name", None)
                self.guild_cache[ctx.guild.id]["dynamic_name"] = None
            if old_name:
                return await ctx.send(f"Name reset to default.\nPrevious name: `{old_name}`")
            else:
                return await ctx.send("Name reset to default.")
        async with self.config.guild(ctx.guild).all() as guild_settings:
            old_name = guild_settings["dynamic_name"]
            dynamic_enabled = guild_settings["dynamic"]
            guild_settings["dynamic_name"] = new_name
            self.guild_cache[ctx.guild.id]["dynamic_name"] = new_name
        msg = [f"Name set to `{new_name}`"]
        if old_name:
            msg.append(f"Previous name: `{old_name}`")
        if not dynamic_enabled:
            msg.append("Remember to enable dynamic channel creation for this to take effect.")
        await ctx.send("\n".join(msg))

    @invoice.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def mute(self, ctx: commands.Context, *, true_or_false: bool = None):
        """
        Toggle whether to modify permissions when a user is server muted.

        If a text channel is set, the user will no longer be able to send messages in it.
        Otherwise, the roles controlled by this cog will be removed.
        Self mutes are unaffected by this setting.
        """
        if true_or_false is None:
            true_or_false = not self.guild_cache[ctx.guild.id]["mute"]
        await self.config.guild(ctx.guild).mute.set(true_or_false)
        self.guild_cache[ctx.guild.id]["mute"] = true_or_false
        await ctx.send(
            "I will {} modify permissions when a user is server muted.".format(
                "now" if true_or_false else "no longer"
            )
        )

    @invoice.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def deaf(self, ctx: commands.Context, *, true_or_false: bool = None):
        """
        Toggle whether to modify permissions when a user is server deafened.

        If a role is set, it will be removed.
        Otherwise, the user will no longer be able to send messages in the set text channel.
        Self deafens are unaffected by this setting.
        """
        if true_or_false is None:
            true_or_false = not self.guild_cache[ctx.guild.id]["deaf"]
        await self.config.guild(ctx.guild).deaf.set(true_or_false)
        self.guild_cache[ctx.guild.id]["deaf"] = true_or_false
        await ctx.send(
            "I will {} modify permissions when a user is server deafened.".format(
                "now" if true_or_false else "no longer"
            )
        )

    @invoice.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def selfdeaf(self, ctx: commands.Context, *, true_or_false: bool = None):
        """
        Toggle whether to modify permissions when a user is self deafened.

        If a role is set, it will be removed.
        Otherwise, the user will no longer be able to send messages in the set text channel.
        Server deafens are unaffected by this setting.
        """
        if true_or_false is None:
            true_or_false = not self.guild_cache[ctx.guild.id]["self_deaf"]
        await self.config.guild(ctx.guild).self_deaf.set(true_or_false)
        self.guild_cache[ctx.guild.id]["self_deaf"] = true_or_false
        await ctx.send(
            "I will {} modify permissions when a user is self deafened.".format(
                "now" if true_or_false else "no longer"
            )
        )

    @invoice.command(aliases=["server"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def guild(self, ctx: commands.Context, *, role: discord.Role = None):
        """
        Set a guild-wide role for users who are in any non-AFK voice channel.
        """
        if not role:
            await self.config.guild(ctx.guild).role.clear()
            self.guild_cache[ctx.guild.id]["role"] = self._guild_defaults()["role"]
            await ctx.send("Role cleared.")
        else:
            await self.config.guild(ctx.guild).role.set(role.id)
            self.guild_cache[ctx.guild.id]["role"] = role.id
            await ctx.send("Role set to {role}.".format(role=role))

    @invoice.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def link(
        self,
        ctx: commands.Context,
        vc: typing.Optional[discord.VoiceChannel] = None,
        *,
        role_or_channel: typing.Union[discord.Role, discord.TextChannel, None] = None,
    ):
        """
        Links a role or text channel to a voice channel.

        When a member joins or leaves the channel, the role is applied accordingly.

        As well, if the related settings are enabled:
            When a member becomes deafened or undeafened, the role is applied accordingly.
            When a member becomes server muted or unmuted, the channel permissions are updated accordingly.

        If a role or channel is not set, the bot will update the other instead.
        """
        if not vc:
            if not ctx.author.voice:
                raise commands.MissingRequiredArgument(
                    # pylint: disable=no-member
                    inspect.signature(self.link.callback).parameters["vc"]
                )
            vc = ctx.author.voice.channel
        assert vc
        if not role_or_channel:
            await self.config.channel(vc).clear()
            del self.channel_cache[vc.id]
            await ctx.send("Link(s) for {vc} cleared.".format(vc=vc))
        elif isinstance(role_or_channel, discord.Role):
            await self.config.channel(vc).role.set(role_or_channel.id)
            self.channel_cache[vc.id]["role"] = role_or_channel.id
            await ctx.send("Role for {vc} set to {role}.".format(vc=vc, role=role_or_channel))
        else:
            if vc == ctx.guild.afk_channel:
                return await ctx.send("Text channels cannot be linked to the guild's AFK channel.")
            await self.config.channel(vc).channel.set(role_or_channel.id)
            self.channel_cache[vc.id]["channel"] = role_or_channel.id
            await ctx.send(
                "Text channel for {vc} set to {channel}".format(vc=vc, channel=role_or_channel)
            )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, vc):
        if not isinstance(vc, discord.VoiceChannel):
            return
        guild = vc.guild
        if vc.category:
            perms = vc.category.permissions_for(guild.me)
        else:
            perms = guild.me.guild_permissions
        # 0x10000010: manage_roles & manage_channels
        if perms.value & 0x10000010 != 0x10000010:
            return
        await self.cog_ready.wait()
        if not self.guild_cache[guild.id]["dynamic"]:
            return
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        if self.guild_as[guild.id].spammy:
            return
        self.dynamic_ready[vc.id] = asyncio.Event()
        guild_role = guild.get_role(self.guild_cache[guild.id]["role"])
        if dynamic_name := self.guild_cache[guild.id]["dynamic_name"]:
            name = re.sub(r"(?i){vc}", vc.name, dynamic_name)
        else:
            name = "\N{SPEAKER WITH THREE SOUND WAVES} " + vc.name
        role = await guild.create_role(
            name=name,
            permissions=guild_role.permissions if guild_role else discord.Permissions.none(),
            reason="Dynamic role for {vc}".format(vc=vc),
        )
        await self.config.channel(vc).role.set(role.id)
        self.channel_cache[vc.id]["role"] = role.id
        if vc.category:
            overs = vc.category.overwrites
        else:
            overs = {}
        # inherit guild role and remove its overwrites
        default = overs.pop(guild_role, discord.PermissionOverwrite())
        # prevent any other roles from viewing the channel
        for overwrite in overs.values():
            overwrite.update(read_messages=None)
        # vc-specific role, inherited from guild role
        overs.setdefault(role, default).update(read_messages=True, send_messages=True)
        # @everyone, remove read permissions
        overs.setdefault(guild.default_role, discord.PermissionOverwrite()).update(
            read_messages=False
        )
        # add bot to the channel
        overs.setdefault(guild.me, discord.PermissionOverwrite()).update(
            read_messages=True, send_messages=True
        )
        text = await guild.create_text_channel(
            name=name,
            overwrites=overs,
            category=vc.category,
            reason="Dynamic channel for {vc}".format(vc=vc),
        )
        await self.config.channel(vc).channel.set(text.id)
        self.channel_cache[vc.id]["channel"] = text.id
        self.guild_as[guild.id].stamp()
        self.dynamic_ready[vc.id].set()

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, vc):
        if not isinstance(vc, discord.VoiceChannel):
            return
        guild = vc.guild
        try:
            settings = self.channel_cache.pop(vc.id)
        except KeyError:
            return
        await self.config.channel(vc).clear()
        await self.cog_ready.wait()
        if not self.guild_cache[guild.id]["dynamic"]:
            return
        role = guild.get_role(settings["role"])
        if role:
            await role.delete(reason="Dynamic role for {vc}".format(vc=vc))
        channel = guild.get_channel(settings["channel"])
        if channel:
            await channel.delete(reason="Dynamic channel for {vc}".format(vc=vc))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, m: discord.Member, b: discord.VoiceState, a: discord.VoiceState
    ) -> None:
        if m.bot:
            return
        if not b.channel and not a.channel:
            return  # I doubt this could happen, but just in case
        if await self.bot.cog_disabled_in_guild(self, m.guild):
            return
        await self.cog_ready.wait()
        role_set: Set[Optional[discord.Role]] = set(m.roles)
        guild_role = m.guild.get_role(self.guild_cache[m.guild.id]["role"])
        if b.channel != a.channel and b.channel:
            channel_cache = self.channel_cache[b.channel.id]
            role_set.discard(m.guild.get_role(channel_cache["role"]))
            if (btc := m.guild.get_channel(channel_cache["channel"])) and m in btc.overwrites:
                try:
                    await btc.set_permissions(target=m, overwrite=None, reason="invoice")
                except discord.NotFound:
                    return
        # This event gets triggered when a member leaves the server,
        # but before the on_member_leave event updates the cache.
        # So, I suppress the exception to save Dav's logs.
        with contextlib.suppress(discord.NotFound):
            await self.apply_permissions(m, a, guild_role, role_set)

    async def apply_permissions(
        self,
        m: discord.Member,
        a: discord.VoiceState,
        guild_role: Optional[discord.Role],
        role_set: Set[Optional[discord.Role]],
    ) -> None:
        if not a.channel or a.afk:
            role_set.discard(guild_role)
            atc, atc_overs = None, None
        else:
            if event := self.dynamic_ready.get(a.channel.id):
                await event.wait()
                if m not in a.channel.members:
                    return
                self.dynamic_ready.pop(a.channel.id, None)
            is_spammy = self.member_as[(m.guild.id, m.id)].spammy
            if not is_spammy:
                role_set.add(guild_role)
            else:
                role_set.discard(guild_role)
            channel_cache = self.channel_cache[a.channel.id]
            guild_cache = self.guild_cache[m.guild.id]
            after_role = m.guild.get_role(channel_cache["role"])
            if not is_spammy:
                role_set.add(after_role)
            atc = m.guild.get_channel(channel_cache["channel"])
            atc_overs = atc.overwrites_for(m) if atc else None
            mute = (a.mute and guild_cache["mute"]) or is_spammy
            if atc:
                assert atc_overs
                flag = False if mute or is_spammy else None
                atc_overs.update(send_messages=flag, add_reactions=flag)
            elif after_role:
                if mute:
                    role_set.discard(after_role)
                else:
                    role_set.add(after_role)
            deaf = (a.deaf and guild_cache["deaf"]) or (a.self_deaf and guild_cache["self_deaf"])
            if after_role:
                if deaf:
                    role_set.discard(after_role)
                else:
                    role_set.add(after_role)
            elif atc:
                assert atc_overs
                atc_overs.update(read_messages=False if deaf or is_spammy else None)
        stamp = False
        role_set.discard(None)
        if role_set.symmetric_difference(m.roles):
            await m.edit(roles=role_set)
            stamp = True
        if atc and atc_overs and atc.overwrites_for(m) != atc_overs:
            await atc.set_permissions(m, overwrite=None if atc_overs.is_empty() else atc_overs)
            stamp = True
        if stamp:
            self.member_as[(m.guild.id, m.id)].stamp()
