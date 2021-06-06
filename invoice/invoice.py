import asyncio
import contextlib
import itertools
import logging
import operator
import re
from dataclasses import asdict, dataclass, fields
from datetime import timedelta
from functools import partial
from typing import (
    Any,
    ChainMap,
    DefaultDict,
    Dict,
    Final,
    Mapping,
    Optional,
    Set,
    Tuple,
    TypedDict,
    TypeVar,
    Union,
    cast,
)

import discord
from proxyembed import ProxyEmbed
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.antispam import AntiSpam
from redbot.core.utils.chat_formatting import humanize_list

from .converter import AsCFIdentifier, DataclassConverter

LOG: Final = logging.getLogger("red.fluffy.invoice")
GuildVoice = Union[discord.VoiceChannel, discord.StageChannel]
GuildVoiceTypes: Final = (discord.VoiceChannel, discord.StageChannel)


class Settings(TypedDict):
    role: Optional[int]
    channel: Optional[int]
    dynamic: Optional[bool]
    dynamic_name: Optional[str]
    mute: Optional[bool]
    deaf: Optional[bool]
    self_deaf: Optional[bool]
    suppress: Optional[bool]


@dataclass
class SettingsConverter(DataclassConverter):
    __total__ = False

    role: discord.Role
    channel: discord.TextChannel
    dynamic: bool
    dynamic_name: str
    mute: bool
    deaf: bool
    self_deaf: bool
    suppress: bool


assert {f.name for f in fields(SettingsConverter)} == set(Settings.__annotations__)
Cache = DefaultDict[int, Settings]
T = TypeVar("T")
MT = TypeVar("MT", bound=Mapping)


class Chain(ChainMap[str, Any]):
    @staticmethod
    def _filter_value(d) -> dict:
        try:
            items = d.items()  # type: ignore
        except AttributeError:
            items = d
        return dict(filter(operator.itemgetter(1), items))  # type: ignore

    @classmethod
    def from_scope(
        cls, scope: Union[GuildVoice, discord.CategoryChannel, discord.Guild], cache: Cache
    ):
        if category_id := getattr(scope, "category_id", None):
            assert isinstance(scope, GuildVoiceTypes) and isinstance(category_id, int)
            return cls(
                cls._filter_value(cache[scope.id]),
                cls._filter_value(cache[category_id]),
                cache[scope.guild.id],
            )
        elif guild := getattr(scope, "guild", None):
            assert isinstance(guild, discord.Guild) and not isinstance(scope, discord.Guild)
            if scope.type == discord.ChannelType.category:
                assert isinstance(scope, discord.CategoryChannel)
                return cls({}, cls._filter_value(cache[scope.id]), cache[guild.id])
            else:
                assert isinstance(scope, GuildVoiceTypes)
                return cls(cls._filter_value(cache[scope.id]), {}, cache[guild.id])
        else:
            assert isinstance(scope, discord.Guild)
            return cls(cache[scope.id])

    def all(self, key):
        return [m.get(key) for m in self.maps]


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

    @staticmethod
    def _debug_and_return(message: str, obj: T) -> T:
        LOG.debug(message, obj)
        return obj

    @staticmethod
    def _is_afk(voice: discord.VoiceState):
        if not voice.channel:
            return None
        assert isinstance(voice.channel, GuildVoiceTypes)
        return voice.channel == voice.channel.guild.afk_channel

    def __init__(self, bot: Red):
        self.bot: Final = bot
        self.config: Final = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_guild(**self._defaults())
        self.config.register_channel(**self._defaults())
        self.cache: Final = Cache(self._defaults)
        self.guild_as: Final[DefaultDict[int, AntiSpam]] = DefaultDict(
            partial(AntiSpam, self.intervals)
        )
        self.member_as: Final[DefaultDict[Tuple[int, int], AntiSpam]] = DefaultDict(
            partial(AntiSpam, self.intervals)
        )
        self.dynamic_ready: Final[Dict[int, asyncio.Event]] = {}
        self.cog_ready: Final = asyncio.Event()
        asyncio.create_task(self.initialize())

    async def initialize(self):
        self.cache.update(await self.config.all_guilds())
        # Default channels before discord removed them shared their IDs with their guild,
        # which would theoretically cause a key conflict here. However,
        # default channels are text channels and these are everything but.
        self.cache.update(await self.config.all_channels())
        self.cog_ready.set()

    @staticmethod
    def _defaults() -> Settings:
        # using __annotations__ directly here since we don't need to eval the annotations
        return cast(Settings, dict.fromkeys(Settings.__annotations__.keys(), None))

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        await self.cog_ready.wait()

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def invoice(self, ctx: commands.GuildContext):
        """
        Configure or view settings for automated voice-based permissions.
        """

    @invoice.command(name="unset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _unset(
        self,
        ctx: commands.GuildContext,
        scope: Optional[Union[discord.CategoryChannel, GuildVoice]],
        *settings: AsCFIdentifier,
    ):
        """
        Unset various settings, causing them to fall back to the outer scope.

        `scope` is the voice channel or category that you wish to change;
        leave it empty to manage guild-wide settings.
        Unset guild-wide settings tell the bot to take no action.

        See `[p]help invoice set` for info on the various settings available.
        """
        if invalid := set(settings).difference(Settings.__annotations__.keys()):
            raise commands.UserInputError("Invalid settings: " + humanize_list(list(invalid)))
        scoped = scope or ctx.guild
        if scope:
            config = self.config.channel(scope)
        else:
            config = self.config.guild(ctx.guild)
        cache = self.cache[scoped.id]
        cache.update(dict.fromkeys(settings, None))  # type: ignore
        if any(cache.values()):
            async with config.all() as conf:
                for k in settings:
                    conf.pop(k, None)
        else:
            # no need to keep this around anymore
            await config.clear()
        await self.show(ctx, scope=scope)

    @invoice.group(name="set", invoke_without_command=True, ignore_extra=False)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _set(
        self,
        ctx: commands.GuildContext,
        scope: Optional[Union[discord.CategoryChannel, GuildVoice]],
        *,
        settings: SettingsConverter,
    ):
        """
        Configure various settings.

        `scope` is the voice channel or category that you wish to change;
        leave it empty to manage guild-wide settings.

        When acting, the bot will act using the narrowest applicable settings.
        E.g., if both the category and the guild have a role set, but the voice channel does not,
        the bot will only apply the category's role, and not the guild's role.

        Configurable settings:
            role:\tThe role that will be applied to users inside the scoped VCs.

            channel:\tThe text channel that users will be granted access to while inside the scoped VCs.

            dynamic:\ttrue/false, whether to dynamically create a new role and text channel for VC users when new VCs are created in this scope.
                The new role will inherit the permissions of the guild-wide role, if there is one,
                and the text channel will inherit the permissions of the category the new channel is in.
                For voice channels, this controls whether the associated role and channel are deleted when the VC is deleted.

            dynamic_name:\tThe name to apply to dynamically created roles and text channels.
                Use `{vc}` as a placeholder to insert the name of the newly created voice channel.
                Defaults to `\N{SPEAKER WITH THREE SOUND WAVES} {vc}`.
                \\* This setting has no effect when set on voice channels.

            mute:\ttrue/false, whether to restrict a user's permissions to send messages in the text channel if server muted.

            deaf:\ttrue/false, whether to restrict a user's permissions to read messages in the text channel if server deafened.

            self_deaf:\ttrue/false, whether to restrict a user's permissions to read messages in the text channel if self-deafened.

            suppress:\ttrue/false, whether to restrict a user's permissions to send messages in the text channel if they don't have permission to speak.
        """
        scoped = scope or ctx.guild
        config = self.config.channel(scope) if scope else self.config.guild(ctx.guild)
        decomposed = {
            k: getattr(v, "id", v)
            for k, v in asdict(settings).items()
            if v is not settings.MISSING
        }
        self.cache[scoped.id].update(decomposed)  # type: ignore
        async with config.all() as conf:
            assert isinstance(conf, dict)
            conf.update(decomposed)
        await self.show(ctx, scope=scope)

    @_set.command(aliases=["showsettings"])
    @commands.guild_only()
    async def show(
        self,
        ctx: commands.GuildContext,
        *,
        scope: Union[discord.CategoryChannel, GuildVoice] = None,
    ):
        guild = ctx.guild
        scoped = scope or guild
        chain = Chain.from_scope(scoped, self.cache)
        embed = ProxyEmbed(title=f"Current settings for {scoped}", color=await ctx.embed_color())
        for key, value in chain.items():
            if value and key == "role":
                value = guild.get_role(value) or "<Deleted role>"
            elif value and key == "channel":
                value = guild.get_channel(value) or "<Deleted channel>"
            elif not value and key == "dynamic_name":
                value = "\N{SPEAKER WITH THREE SOUND WAVES} {vc}"
            else:
                value = value or False
            embed.add_field(
                name=key.replace("_", " ").title(), value=getattr(value, "mention", str(value))
            )
        embed.set_footer(
            text="Settings shown here reflect the effective settings for the scope,"
            " including inherited settings from the category or guild."
        )
        await embed.send_to(ctx, allowed_mentions=discord.AllowedMentions(users=False))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, vc: discord.abc.GuildChannel):
        if not isinstance(vc, GuildVoiceTypes):
            return
        guild = vc.guild
        if vc.category:
            perms = vc.category.permissions_for(guild.me)
        else:
            perms = guild.me.guild_permissions
        # 0x10000010: manage_roles & manage_channels
        if perms.value & 0x10000010 != 0x10000010:
            return
        if self.guild_as[guild.id].spammy:
            return
        if await self.bot.cog_disabled_in_guild(self, guild):
            return
        await self.cog_ready.wait()
        chain = Chain.from_scope(vc, self.cache)
        if not chain["dynamic"]:
            return
        self.dynamic_ready[vc.id] = asyncio.Event()
        try:
            scoped_roles = list(filter(None, map(guild.get_role, chain.all("role"))))
            if dynamic_name := chain["dynamic_name"]:
                name = re.sub(r"(?i){vc}", vc.name, dynamic_name)
            else:
                name = "\N{SPEAKER WITH THREE SOUND WAVES} " + vc.name
            perms = discord.Permissions.none()
            for role in scoped_roles:
                perms.value |= role.permissions.value
            perms.value &= guild.me.guild_permissions.value
            role = await guild.create_role(
                name=name,
                permissions=perms,
                reason="Dynamic role for {vc}".format(vc=vc),
            )
            chain["role"] = role.id
            await self.config.channel(vc).role.set(role.id)
            if vc.category:
                overs = vc.category.overwrites
            else:
                overs = {}
            # inherit scoped roles and remove their permissions
            deny, allow = discord.Permissions.none(), discord.Permissions.none()
            for role in scoped_roles:
                try:
                    o_allow, o_deny = overs.pop(role).pair()
                    deny.value |= o_deny.value
                    allow.value |= o_allow.value
                except KeyError:
                    pass
            default = discord.PermissionOverwrite.from_pair(allow, deny)
            # prevent any other roles from viewing the channel
            for k, overwrite in overs.copy().items():
                overwrite.update(read_messages=None)
                if overwrite.is_empty():
                    del overs[k]
            # let admins and mods see the channel
            for role in itertools.chain(
                await self.bot.get_admin_roles(guild), await self.bot.get_mod_roles(guild)
            ):
                overs.setdefault(role, discord.PermissionOverwrite()).update(read_messages=True)
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
        finally:
            self.guild_as[guild.id].stamp()
            self.dynamic_ready[vc.id].set()

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, vc):
        if not isinstance(vc, GuildVoiceTypes):
            return
        guild = vc.guild
        await self.cog_ready.wait()
        await self.config.channel(vc).clear()
        chain = Chain.from_scope(vc, self.cache)
        try:
            settings = self.cache.pop(vc.id)
        except KeyError:
            return
        if not chain["dynamic"]:
            return
        role = guild.get_role(settings["role"])
        if role:
            await role.delete(reason=f"Dynamic role for {vc}")
        channel = guild.get_channel(settings["channel"])
        if channel:
            await channel.delete(reason=f"Dynamic channel for {vc}")

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
        LOG.debug("on_voice_state_update(%s, %s, %s)", m, b, a)
        await self.cog_ready.wait()
        role_set: Set[int] = set(m._roles)  # type: ignore
        channel_updates: Dict[int, Optional[discord.PermissionOverwrite]] = {}
        if b.channel != a.channel and b.channel:
            self._remove_before(b, role_set, channel_updates)

        if self._is_afk(a) is False and not self.member_as[(m.guild.id, m.id)].spammy:
            assert isinstance(a.channel, GuildVoiceTypes)
            try:
                await self.dynamic_ready[a.channel.id].wait()
            except KeyError:
                pass
            self._add_after(a, role_set, channel_updates)

        # This event gets triggered when a member leaves the server,
        # but before the on_member_leave event updates the cache.
        # So, I suppress the exception to save Dav's logs.
        with contextlib.suppress(discord.NotFound):
            await self.apply_permissions(m, role_set, channel_updates)

    def _remove_before(
        self,
        b: discord.VoiceState,
        role_set: Set[int],
        channel_updates: Dict[int, Optional[discord.PermissionOverwrite]],
    ):
        assert isinstance(b.channel, GuildVoiceTypes)
        chain = Chain.from_scope(b.channel, self.cache)
        role_set.difference_update(
            self._debug_and_return("maybe removing role IDs: %s", chain.all("role"))
        )
        channel_id: int
        for channel_id in filter(
            None,
            self._debug_and_return("maybe removing channel overwrites: %s", chain.all("channel")),
        ):
            channel_updates[channel_id] = None

    def _add_after(
        self,
        a: discord.VoiceState,
        role_set: Set[int],
        channel_updates: Dict[int, Optional[discord.PermissionOverwrite]],
    ):
        assert isinstance(a.channel, GuildVoiceTypes)
        guild = a.channel.guild
        chain = Chain.from_scope(a.channel, self.cache)
        role_id: int = next(filter(guild.get_role, chain.all("role")), 0)
        channel_id: int = next(filter(guild.get_channel, chain.all("channel")), 0)
        if role_id:
            LOG.debug("Pre-emptively adding role: %s", role_id)
            role_set.add(role_id)
            overwrites = discord.PermissionOverwrite()
        elif channel_id:
            LOG.debug("Pre-emptively adding read_messages: %s", channel_id)
            overwrites = discord.PermissionOverwrite(read_messages=True)
        else:
            # nothing to do
            return
        mute: bool = a.mute and chain["mute"]
        deaf: bool = a.deaf and chain["deaf"]
        self_deaf: bool = a.self_deaf and chain["self_deaf"]
        suppress: bool = a.suppress and chain["suppress"]
        LOG.debug(
            "mute: %s, suppress: %s, deaf: %s, self_deaf: %s", mute, suppress, deaf, self_deaf
        )
        if mute or suppress:
            LOG.debug("muted or suppressed")
            if channel_id:
                overwrites.update(send_messages=False, add_reactions=False)
            else:
                role_set.discard(role_id)
        if deaf or self_deaf:
            if role_id:
                role_set.discard(role_id)
            else:
                overwrites.update(read_messages=False)
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug(
                "role: %s; overwrites: allow %s, deny %s",
                role_id in role_set,
                *map(Chain._filter_value, overwrites.pair()),
            )
        channel_updates[channel_id] = None if overwrites.is_empty() else overwrites

    async def apply_permissions(
        self,
        m: discord.Member,
        role_set: Set[int],
        channel_updates: Dict[int, Optional[discord.PermissionOverwrite]],
    ) -> None:
        guild = m.guild
        stamp = False
        if role_set.symmetric_difference(m._roles):  # type: ignore
            my_top_role = guild.me.top_role

            def filt(r: Optional[discord.Role]):
                return r and my_top_role > r

            await m.edit(roles=list(filter(filt, map(guild.get_role, role_set))))  # type: ignore
            stamp = True
        for channel_id, overs in channel_updates.items():
            if (channel := guild.get_channel(channel_id)) and channel.overwrites.get(m) != overs:
                await channel.set_permissions(m, overwrite=overs)
                stamp = True
        if stamp:
            self.member_as[(m.guild.id, m.id)].stamp()
