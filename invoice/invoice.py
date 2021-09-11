import asyncio
import builtins
import contextlib
import itertools
import logging
import operator
import re
from dataclasses import dataclass, fields
from datetime import timedelta
from functools import partial
from typing import (
    Any,
    Callable,
    ChainMap,
    DefaultDict,
    Dict,
    Final,
    List,
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

from .converter import AsCFIdentifier, DataclassConverter, asdict_shallow

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
_KT = TypeVar("_KT")
_VT = TypeVar("_VT")
_T = TypeVar("_T")


def _filter_none(d: Mapping[_KT, Optional[_VT]]) -> Dict[_KT, _VT]:
    return {k: v for k, v in d.items() if v is not None}


def _filter_value(d, filterer: Callable[[Any], bool] = operator.itemgetter(1)) -> dict:
    try:
        items = d.items()  # type: ignore
    except AttributeError:
        items = d
    return dict(filter(filterer, items))  # type: ignore


class Chain(ChainMap[str, Any]):
    @classmethod
    def from_scope(
        cls, scope: Union[GuildVoice, discord.CategoryChannel, discord.Guild], cache: Cache
    ):
        if category_id := getattr(scope, "category_id", None):
            assert isinstance(scope, GuildVoiceTypes) and isinstance(category_id, int)
            return cls(
                _filter_none(cache[scope.id]),
                _filter_none(cache[category_id]),
                cache[scope.guild.id],
            )
        elif guild := getattr(scope, "guild", None):
            assert isinstance(guild, discord.Guild) and not isinstance(scope, discord.Guild)
            if scope.type == discord.ChannelType.category:
                assert isinstance(scope, discord.CategoryChannel)
                return cls({}, _filter_none(cache[scope.id]), cache[guild.id])
            else:
                assert isinstance(scope, GuildVoiceTypes)
                return cls(_filter_none(cache[scope.id]), {}, cache[guild.id])
        else:
            assert isinstance(scope, discord.Guild)
            return cls(cache[scope.id])

    def all(self, key, *, map=None):
        if not map:

            def map(arg):
                return arg

        _map = builtins.map
        return list(filter(None, _map(map, (m.get(key) for m in self.maps))))


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
    def _debug_and_return(message: str, obj: _T) -> _T:
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

    @invoice.command(name="unset", require_var_positional=True)
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

        __Configurable Settings__
        **role**:\tThe role granted to users inside the scoped VCs.
        **channel**:\tThe text channel granted access to while inside the scoped VCs.
        **dynamic**:\ttrue/false, create a new role and text channel when new VCs are created here.
            The new role will inherit the permissions of higher-scoped roles.
        **dynamic_name**:\tThe name to apply to dynamically created roles and text channels.
            `{vc}` will be replaced with the name of the new channel.
        **mute**:\ttrue/false, mute the user in the text channel if they are server muted.
        **suppress**:\ttrue/false, mute the user in the text channel if they don't have permission to speak.
        **deaf**:\ttrue/false, remove the user from the text channel if they are server deafened.
        **self_deaf**:\ttrue/false, remove the user from the text channel if they are self deafened.
        """
        scoped = scope or ctx.guild
        config = self.config.channel(scope) if scope else self.config.guild(ctx.guild)
        decomposed = {
            k: getattr(v, "id", v)
            for k, v in asdict_shallow(settings).items()
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
        """
        Show the current settings for the specified scope.

        See `[p]help invoice set` for explanations of the various settings.
        """
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
        # TODO: split this code into smaller functions
        if not isinstance(vc, GuildVoiceTypes):
            return
        guild = vc.guild
        me = guild.me
        my_perms = me.guild_permissions
        # manage_roles & manage_channels & read_messages & send_messages
        if my_perms.value & 0x10000C10 != 0x10000C10:
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
            if dynamic_name := chain["dynamic_name"]:
                name = re.sub(r"(?i){vc}", vc.name, dynamic_name)
            else:
                name = "\N{SPEAKER WITH THREE SOUND WAVES} " + vc.name
            scoped_roles: List[discord.Role] = chain.all("role", map=guild.get_role)
            if scoped_roles:
                perms = scoped_roles[0].permissions
            else:
                perms = discord.Permissions.none()
            perms.value &= my_perms.value
            dynamic_role = await guild.create_role(
                name=name,
                permissions=perms,
                reason="Dynamic role for {vc}".format(vc=vc),
            )
            await self.config.channel(vc).role.set(dynamic_role.id)
            self.cache[vc.id]["role"] = dynamic_role.id
            # assume my_perms doesn't have Manage Roles for channel creation if not admin
            # because: https://discord.com/developers/docs/resources/guild#create-guild-channel
            my_perms.manage_roles = my_perms.administrator
            # also if your bot actually has admin... why...
            if vc.category:
                overs = vc.category.overwrites
            else:
                overs = {}
            # inherit scoped roles and remove their permissions
            allow, deny = discord.Permissions(read_messages=True), discord.Permissions.none()
            for role in scoped_roles:
                try:
                    o_allow, o_deny = overs.pop(role).pair()
                except KeyError:
                    pass
                else:
                    deny.value |= o_deny.value
                    allow.value |= o_allow.value
            # ensure default can be applied by the bot on creation
            allow.value &= my_perms.value
            deny.value &= my_perms.value
            default = discord.PermissionOverwrite.from_pair(allow, deny)
            # now assume we don't have read_messages
            # makes the following code simpler
            my_perms.read_messages = False
            # prevent any other roles from having read_messages,
            # and also ensure that the overwrites can be applied on creation
            for k, overwrite in overs.copy().items():
                o_allow, o_deny = overwrite.pair()
                o_allow.value &= my_perms.value
                o_deny.value &= my_perms.value
                overwrite = discord.PermissionOverwrite.from_pair(o_allow, o_deny)
                if overwrite.is_empty():
                    del overs[k]
                else:
                    overs[k] = overwrite
            # now apply the vc-specific role
            overs[dynamic_role] = default
            # let admins and mods see the channel
            for role in itertools.chain(
                await self.bot.get_admin_roles(guild), await self.bot.get_mod_roles(guild)
            ):
                overs.setdefault(role, discord.PermissionOverwrite()).update(read_messages=True)
            # deny read permissions from @everyone
            overs.setdefault(guild.default_role, discord.PermissionOverwrite()).update(
                read_messages=False
            )
            # add bot to the channel
            key: Union[discord.Member, discord.Role] = me
            for role in me.roles:
                if role.tags and role.tags.bot_id == me.id:
                    key = role
                    break
            overs.setdefault(key, discord.PermissionOverwrite()).update(
                read_messages=True, manage_channels=True
            )
            text = await guild.create_text_channel(
                name=name,
                overwrites=overs,
                category=vc.category,
                reason="Dynamic channel for {vc}".format(vc=vc),
            )
            await self.config.channel(vc).channel.set(text.id)
            self.cache[vc.id]["channel"] = text.id
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
                *map(_filter_value, overwrites.pair()),
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
        role_set.discard(guild.id)
        if role_set.symmetric_difference(m._roles):  # type: ignore
            try:
                await m.edit(roles=[discord.Object(id) for id in role_set])
            except discord.Forbidden:
                LOG.info("Unable to edit roles for %s in guild %s", m, guild)
                LOG.debug("Before: %s\nAfter: %s", m._roles, role_set)
            stamp = True
        for channel_id, overs in channel_updates.items():
            if (channel := guild.get_channel(channel_id)) and channel.overwrites.get(m) != overs:
                try:
                    await channel.set_permissions(m, overwrite=overs)
                except discord.Forbidden:
                    LOG.info("Unable to edit channel permissions for %s in guild %s", m, guild)
                stamp = True
        if stamp:
            self.member_as[(m.guild.id, m.id)].stamp()
