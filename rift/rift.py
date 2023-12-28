import asyncio
import logging
from datetime import datetime, timedelta, timezone
from functools import partial
from io import BytesIO
from itertools import starmap
from traceback import walk_tb
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Dict,
    Hashable,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
    cast,
    overload,
)

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils import AsyncIter, deduplicate_iterables, mod
from redbot.core.utils.chat_formatting import humanize_list, pagify, quote
from redbot.core.utils.common_filters import filter_invites
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.predicates import MessagePredicate

from .graph import SimpleGraph, Vector

if TYPE_CHECKING:
    from discord.abc import Messageable
    from redbot.cogs.filter import Filter

    _H = TypeVar("_H", bound=Hashable)

    def deduplicate_iterables(*iterables: Iterable[_H]) -> List[_H]:  # noqa: F811
        ...

else:
    from .converter import DiscordConverter as Messageable

from .converter import Limited

log = logging.getLogger("red.fluffy.rift")
_ = Translator(__name__, __file__)
T = TypeVar("T")
UnionUser = Union[discord.User, discord.Member]
UnionChannel = Union[discord.DMChannel, discord.TextChannel]


@overload
async def can_close(ctx: commands.Context) -> bool:
    ...


@overload
async def can_close(ctx: discord.Message, bot: Red) -> bool:
    ...


async def can_close(ctx: Union[commands.Context, discord.Message], bot: Red = None):
    """Admin / manage channel OR private channel"""
    if ctx.channel.type == discord.ChannelType.private:
        return True
    if isinstance(ctx, discord.Message):
        if not bot:
            raise TypeError
        ctx = SimpleNamespace(author=ctx.author, channel=ctx.channel, bot=bot, message=ctx)
    return await mod.is_admin_or_superior(ctx.bot, ctx.author) or await mod.check_permissions(
        ctx, {"manage_channels": True}
    )


def check_can_close(func=None):
    check = commands.permissions_check(can_close)
    if func:
        return check(func)
    return check


async def purge_ids(
    channel: discord.abc.Messageable,
    ids: Iterable[int],
    now: Union[datetime, discord.abc.Snowflake],
):
    channel = await channel._get_channel()
    assert isinstance(channel, (discord.TextChannel, discord.DMChannel))
    try:
        nowdt: datetime = now.created_at  # type: ignore
    except AttributeError:
        assert isinstance(now, datetime)
        nowdt = now
    snowflakes = [
        channel.get_partial_message(i)
        for i in ids
        if nowdt - discord.utils.snowflake_time(i) < timedelta(days=14, minutes=-5)
    ]
    if not snowflakes:
        return
    try:
        can_purge = channel.permissions_for(channel.guild.me).manage_messages  # type: ignore
    except AttributeError:
        can_purge = False
    if can_purge:
        assert isinstance(channel, discord.TextChannel)
        while True:
            try:
                await channel.delete_messages(snowflakes[:100])  # type: ignore
            except discord.Forbidden:
                return
            except discord.HTTPException:
                pass
            snowflakes = snowflakes[100:]
            if snowflakes:
                await asyncio.sleep(1.5)
            else:
                return
    else:
        for partial_message in snowflakes:
            try:
                await partial_message.delete()
            except discord.Forbidden:
                return
            except discord.HTTPException:
                pass


class RiftError(Exception):
    pass


@cog_i18n(_)
class Rift(commands.Cog):
    """
    Communicate with other servers/channels.
    """

    async def red_get_data_for_user(self, *, user_id):
        if await self.config.user_from_id(user_id).blacklisted():
            bio = BytesIO(
                (
                    "You are currently blocked from being able to receive rifts "
                    "in your DMs, at your request."
                ).encode("utf-8")
            )
            bio.seek(0)
            return {f"{self.__class__.__name__}.txt": bio}
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        if requester == "discord_deleted_user":
            await self.config.user_from_id(user_id).clear()
        else:
            log.warning(
                "Ignoring deletion request %r, as rift blocklists are designed for anti-abuse.",
                requester,
            )

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.rifts = SimpleGraph[Messageable]()
        self.messages = SimpleGraph[Tuple[Optional[int], int, int]]()
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_channel(blacklisted=False)
        self.config.register_guild(blacklisted=False)
        self.config.register_user(blacklisted=False)
        self.config.register_global(
            notify=True,  # format="[{role}] {author}", format_no_guild="{author}"
        )

    # COMMANDS

    @commands.command()
    async def send(self, ctx: commands.Context, *rifts: Messageable):
        """
        Send a message to the specified destinations.

        Editing or deleting the message you send will still forward
        to the bot's reposts, as in normal rifts.
        """
        if not rifts:
            raise commands.UserInputError()
        unique_rifts = deduplicate_iterables(rifts)
        await ctx.send("What would you like to say?")
        p = MessagePredicate.same_context(ctx=ctx)
        message = await ctx.bot.wait_for("message", check=p)
        await self._send(message, unique_rifts)
        await message.reply("Your message has been sent.", mention_author=False)

    @commands.group()
    async def rift(self, ctx: commands.Context):
        """
        Communicate with other channels through Red.
        """

    @rift.group(aliases=["denylist", "blacklist"])
    @check_can_close()
    async def blocklist(self, ctx: commands.Context):
        """
        Configures blocklists.

        Blocklisted destinations cannot have rifts opened to them.
        """

    @blocklist.command(name="channel")
    @check_can_close()
    async def blocklist_channel(
        self, ctx: commands.Context, *, channel: discord.TextChannel = None
    ):
        """
        Blocklists the current channel or the specified channel.

        Can also blocklist DM channels.
        """
        if channel and not ctx.guild:
            raise commands.BadArgument(_("You cannot blocklist a channel in DMs."))
        if not ctx.guild:
            channel = ctx.author
            group = self.config.user(channel)
        else:
            channel = channel or ctx.channel
            group = self.config.channel(channel)
        blacklisted = not await group.blacklisted()
        await group.blacklisted.set(blacklisted)
        await ctx.send(
            _("Channel is {} blocklisted.").format("now" if blacklisted else "no longer")
        )
        if blacklisted:
            await self.close_rifts(ctx.author, channel)

    @blocklist.command(name="server", aliases=["guild"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def blocklist_server(self, ctx: commands.Context):
        """
        Blocklists the current server.

        All channels and members in a server are considered blocklisted if the server is blocklisted.
        Members can still be reached if they are in another, non-blocklisted server.
        """
        group = self.config.guild(ctx.guild)
        blacklisted = not await group.blacklisted()
        await group.blacklisted.set(blacklisted)
        await ctx.send(_("Server is {} denylisted.").format("now" if blacklisted else "no longer"))
        if blacklisted:
            await self.close_rifts(ctx.author, *ctx.guild.text_channels)

    @rift.group(name="close", invoke_without_command=True)
    async def rift_close(self, ctx: commands.Context):
        """
        Closes all rifts that lead to this channel.
        """
        channel = ctx.channel if ctx.guild else ctx.author
        if await can_close(ctx):
            num = await self.close_rifts(ctx.author, channel)
        else:
            num = await self.close_rifts(ctx.author, Limited(message=ctx.message))
        if num:
            await ctx.send(f"{num} rifts that lead here have been closed.")
        else:
            await ctx.send("No rifts were found that lead to here.")

    @rift_close.command(name="guild", aliases=["server"])
    @commands.guild_only()
    @check_can_close()
    async def close_guild(self, ctx: commands.Context):
        """
        Closes all rifts that lead to this server.
        """
        num = await self.close_rifts(ctx.author, *ctx.guild.text_channels)
        if num:
            await ctx.send(f"{num} rifts that lead here have been closed.")
        else:
            await ctx.send("No rifts were found that lead to here.")

    @rift.command(name="notify")
    @checks.is_owner()
    async def rift_notify(self, ctx: commands.Context, *, notify: bool = None):
        """
        Toggle whether the bot notifies the destination of an open rift.

        The notification is only disabled for bot owners, and
        will still notify channels the bot owner doesn't have direct access to.
        """
        if notify is None:
            notify = not await self.config.notify()
        await self.config.notify.set(notify)
        await ctx.send(
            _("I will {} notify destinations when you open new rifts.").format(
                "now" if notify else "no longer"
            )
        )

    @rift.command(name="open", require_var_positional=True)
    async def rift_open(
        self, ctx: commands.Context, one_way: Optional[bool] = None, *rifts: Messageable
    ):
        """
        Opens a rift to the specified destination(s).

        Only your messages will be forwarded to the specified destinations,
        and all replies will be sent back to you.
        """
        unique_rifts: List[Messageable] = deduplicate_iterables(rifts)
        source = Limited(message=ctx.message) if ctx.guild else ctx.author
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        for rift in unique_rifts:
            if (
                no_notify
                and getattr(rift, "guild", None)
                and not isinstance(rift, discord.abc.User)
            ):
                assert isinstance(rift, discord.TextChannel)
                mem = rift.guild.get_member(ctx.author.id)
                if mem and rift.permissions_for(mem).read_messages:
                    notify = False
                else:
                    notify = True
            else:
                notify = True
            self.rifts.add_vectors(source, rift, two_way=not one_way)
            if notify:
                asyncio.ensure_future(
                    rift.send(
                        _("{} has opened a rift to here from {}.").format(
                            ctx.author.global_name, ctx.channel
                        )
                    )
                )
        await ctx.send(
            _(
                "A rift has been opened to {}! Everything you say will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, unique_rifts))))
        )

    @rift.command(name="link", require_var_positional=True)
    @check_can_close()
    async def rift_link(
        self, ctx: commands.Context, one_way: Optional[bool] = None, *rifts: Messageable
    ):
        """
        Links this channel to the specified destination(s).

        Anything anyone says in this channel will be forwarded.
        All replies will be relayed back here.
        """
        unique_rifts: List[Messageable] = deduplicate_iterables(rifts)
        source = ctx.channel if ctx.guild else ctx.author
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        for rift in unique_rifts:
            if (
                no_notify
                and getattr(rift, "guild", None)
                and not isinstance(rift, discord.abc.User)
            ):
                assert isinstance(rift, discord.TextChannel)
                mem = rift.guild.get_member(ctx.author.id)
                if mem and rift.permissions_for(mem).read_messages:
                    notify = False
                else:
                    notify = True
            else:
                notify = True
            self.rifts.add_vectors(source, rift, two_way=not one_way)
            if notify:
                asyncio.ensure_future(
                    rift.send(
                        _("{} has linked a rift to here from {}.").format(
                            ctx.author.global_name, ctx.channel
                        )
                    )
                )
        await ctx.send(
            _(
                "A link has been created to {}! Everything said in this channel will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, unique_rifts))))
        )

    @rift.command(name="web", require_var_positional=True)
    @checks.is_owner()
    async def rift_web(self, ctx: commands.Context, *rifts: Messageable):
        """
        Opens up all possible connections between this channel and the specified rifts.

        See the helptext of `[p]rift link` for more info.
        """
        unique_rifts: List[Messageable] = deduplicate_iterables(rifts)
        source = ctx.channel if ctx.guild else ctx.author
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        self.rifts.add_web(source, *unique_rifts)
        humanized = humanize_list(list(map(str, (source, *unique_rifts))))
        for rift in unique_rifts:
            if (
                no_notify
                and getattr(rift, "guild", None)
                and not isinstance(rift, discord.abc.User)
            ):
                assert isinstance(rift, discord.TextChannel)
                mem = rift.guild.get_member(ctx.author.id)
                if mem and rift.permissions_for(mem).read_messages:
                    notify = False
                else:
                    notify = True
            else:
                notify = True
            if notify:
                asyncio.ensure_future(
                    rift.send(
                        _("{} has opened a web to here, connecting you to {}.").format(
                            ctx.author, humanized
                        )
                    )
                )
        await ctx.send(
            _(
                "A web has been opened to {}! Everything you say will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, unique_rifts))))
        )

    @rift.command(name="info")
    @commands.bot_has_permissions(embed_links=True)
    async def rift_search(self, ctx: commands.Context, *, scope: str = "channel"):
        """
        Provides info about rifts opened in the specified scope.
        """
        author = Limited(message=ctx.message) if ctx.guild else ctx.author
        try:
            scoped = {
                "user": author,
                "member": author,
                "author": author,
                "channel": ctx.channel if ctx.guild else ctx.author,
                "guild": ctx.guild,
                "server": ctx.guild,
                "global": None,
            }[scope.casefold()]
        except KeyError:
            raise commands.BadArgument(
                _("Invalid scope. Scope must be author, channel, guild, server, or global.")
            ) from None

        if not scoped and not await ctx.bot.is_owner(ctx.author):
            raise commands.CheckFailure()
        if scoped == ctx.guild and not await mod.is_admin_or_superior(ctx.bot, ctx.author):
            raise commands.CheckFailure()

        def check(vector):
            if not scoped:
                return True
            if scoped in vector:
                return True
            return scoped in map(lambda c: getattr(c, "guild", None), vector)

        unique_rifts: Set[Vector[Messageable]] = set()
        for source, destination in self.rifts.vectors():
            if check((source, destination)) and (destination, source) not in unique_rifts:
                unique_rifts.add((source, destination))
        total_rifts = len(unique_rifts)
        if not total_rifts:
            return await ctx.send(_("No rifts are connected to this scope."))

        pages: List[discord.Embed] = []
        for i, (source, destination) in enumerate(unique_rifts, 1):
            if source in self.rifts.get(destination, ()):
                delim = "⟷"
            else:
                delim = "⟶"
            embed = discord.Embed(
                title=f"{source} {delim} {destination}", color=await ctx.embed_color()
            )
            if topic := getattr(destination, "topic", None):
                embed.description = topic
            try:
                members = destination.users
            except AttributeError:
                members = destination.members
            # TODO: format and sort members
            member_str = humanize_list(list(map(str, members)))
            short_member_str = next(pagify(member_str, delims=[","]))
            if len(member_str) != len(short_member_str):
                short_member_str += " …"
            embed.add_field(name=f"Connected from {destination}", value=member_str)
            embed.set_footer(text=f"Rift {i} of {total_rifts}")
            pages.append(embed)
        await menu(ctx, pages, DEFAULT_CONTROLS)

    # SPECIAL METHODS

    # UTILITIES

    @staticmethod
    def _to_ids(m) -> Tuple[Optional[int], int, int]:
        return getattr(m.guild, "id", None), m.channel.id, m.id

    def _partial(
        self, guild_id: Optional[int], channel_id: int, message_id: int
    ) -> Optional[discord.PartialMessage]:
        # This function has the potential to miss messages in DMs due to the DMChannel limit
        # Re-opening DMChannels just to edit/delete old messages is, in most cases, unnecessary
        if guild_id is not None:
            try:
                channel = self.bot.get_guild(guild_id).get_channel(channel_id)
            except AttributeError:
                return None
        else:
            channel = self.bot._connection._get_private_channel(channel_id)
        try:
            return channel.get_partial_message(message_id)
        except AttributeError:
            return None

    async def _send(self, message: discord.Message, destinations: List[Messageable]):
        futures = [asyncio.ensure_future(self.process_send(message, d)) for d in destinations]
        if len(futures) == 1:
            fs: Iterable[asyncio.Future] = futures  # no need to wrap this up in as_completed
        else:
            fs = asyncio.as_completed(futures)
        for fut in fs:
            try:
                m = await fut
            except Exception as exc:
                destination = None
                for fr, _line in walk_tb(exc.__traceback__):
                    destination = fr.f_locals.get("destination", destination)
                if not destination:
                    continue
                log.exception(f"Couldn't send {message.id} to {destination}.")
                if isinstance(exc, (RiftError, discord.HTTPException)):
                    reason = " ".join(exc.args)
                else:
                    reason = f"{type(exc).__name__}. Check your console or logs for details."
                await message.channel.send(
                    f"I couldn't send your message to {destination}: {reason}"
                )
            else:
                self.messages.add_vectors(self._to_ids(message), self._to_ids(m))

    async def close_rifts(self, closer: UnionUser, *destinations: Messageable) -> int:
        unique = set()
        for destination in destinations:
            unique.add(destination)
            if not isinstance(destination, (Limited, discord.abc.User)):
                unique.add(Limited(author=closer, channel=destination))
        fmt = _("{closer} has closed a rift to here from {source}.")
        if await self.bot.is_owner(closer):
            notify = await self.config.notify()
        else:
            notify = True

        processed: Set[Vector[Messageable]] = set()
        num_closed = 0
        for source, dest in self.rifts.vectors():
            if (dest, source) in processed:
                continue
            if source in unique:
                if notify:
                    asyncio.ensure_future(
                        dest.send(fmt.format(closer=closer.global_name, source=source))
                    )
                num_closed += 1
            elif dest in unique:
                if notify:
                    asyncio.ensure_future(
                        source.send(fmt.format(closer=closer.global_name, source=dest))
                    )
                num_closed += 1
            processed.add((source, dest))

        self.rifts.remove_vertices(*unique)
        return num_closed

    def get_embed(
        self, attachments: List[discord.Attachment], **kwargs
    ) -> Optional[discord.Embed]:
        if not attachments:
            return None
        if len(attachments) > 25:
            raise RiftError(_("Too many files."))
        try:
            embed = kwargs["embed"]
        except KeyError:
            embed = discord.Embed(**kwargs)
        use_image = True
        for attachment in attachments:
            if (
                use_image
                and attachment.content_type
                and attachment.content_type.startswith("image/")
            ):
                embed.set_image(url=attachment.url)
                use_image = False
            embed.add_field(
                name=self.xbytes(attachment.size),
                value=f"[\N{PAPERCLIP}`{attachment.filename}`]({attachment.url})",
                inline=True,
            )
        return embed

    def get_embeds(self, attachments: List[discord.Attachment], **kwargs) -> List[discord.Embed]:
        """
        Webhook-only. Creates a List of embeds that will display as one multi-image embed.
        """
        if not attachments:
            return []
        if len(attachments) > 10 or len(attachments) == 1:
            embed = self.get_embed(attachments, **kwargs)
            assert embed
            return [embed]
        try:
            embeds = [kwargs["embed"]]
        except KeyError:
            embeds = [discord.Embed(**kwargs)]
        append = False
        for attachment in attachments:
            url = attachment.url
            if attachment.content_type and attachment.content_type.startswith("image/"):
                if append:
                    embed = discord.Embed(url=embeds[0].url)
                    embed.set_image(url=url)
                    embeds.append(embed)
                else:
                    embeds[0].url = embeds[0].url or url
                    embeds[0].set_image(url=url)
                    append = True
            embeds[0].add_field(
                name=self.xbytes(attachment.size),
                value=f"[\N{PAPERCLIP}`{attachment.filename}`]({url})",
                inline=True,
            )
        return embeds

    def permissions(self, destination: UnionChannel, user, is_owner=False):
        if destination.type == discord.ChannelType.private:
            assert isinstance(destination, discord.DMChannel)
            return destination.permissions_for(self.bot.user)
        assert isinstance(destination, discord.TextChannel)
        if is_owner:
            return discord.Permissions.all()
        member = destination.guild.get_member(user.id)
        if member:
            return destination.permissions_for(member)
        every = destination.guild.default_role
        allow, deny = destination.overwrites_for(every).pair()
        perms = (every.permissions.value & ~deny.value) | allow.value
        log.debug(
            "calculated permissions for @everyone in guild %s: %s",
            destination.guild.id,
            perms,
        )
        return discord.Permissions(perms)

    @staticmethod
    def xbytes(b):
        suffix = ""
        for suffix in ("", "K", "M"):  # noqa: B007
            if b <= 900:
                break
            b /= 1024.0
        return f"{b:.3f} {suffix}B"

    async def process_send(self, message: discord.Message, destination: discord.abc.Messageable):
        channel: UnionChannel = await destination._get_channel()
        author = message.author
        kwargs = await self.process_kwargs(
            author,
            channel,
            message.jump_url,
            content=message.content,
            attachments=message.attachments,
        )
        if to_ref := await self.process_reference(message.reference, channel):
            kwargs["reference"] = to_ref
            kwargs["mention_author"] = any(m.id == self.bot.user.id for m in message.mentions)
        return await self.try_or_remove(destination.send(**kwargs), channel)

    async def process_edit(
        self, payload: discord.RawMessageUpdateEvent, destination: discord.PartialMessage
    ):
        channel = destination.channel
        this_guild: Optional[discord.Guild] = payload.guild_id and self.bot.get_guild(
            payload.guild_id
        )
        if this_guild:
            this_channel = this_guild.get_channel(payload.channel_id)
            author = this_guild.get_member(int(payload.data["author"]["id"]))
        else:
            this_channel = self.bot._connection._get_private_channel(payload.channel_id)
            author = self.bot.get_user(int(payload.data["author"]["id"]))
        assert this_channel and author
        kwargs = await self.process_kwargs(
            author,
            channel,
            f"https://discord.com/channels/{payload.guild_id or '@me'}/{payload.channel_id}/{payload.message_id}",
            content=payload.data.get("content"),
            attachments=[
                cast(discord.Attachment, SimpleNamespace(**attachment))
                for attachment in payload.data.get("attachments", ())
            ],
        )
        try:
            return await destination.edit(**kwargs)
        except discord.HTTPException:
            pass

    async def process_reference(
        self,
        reference: Optional[discord.MessageReference],
        channel: UnionChannel,
    ) -> Optional[discord.MessageReference]:
        if not reference:
            return None
        async for de, to in AsyncIter(self.messages.vectors(), steps=100):
            if (
                de == (reference.guild_id, reference.channel_id, reference.message_id)
                and to[1] == channel.id
            ):
                return discord.MessageReference(
                    guild_id=to[0],
                    channel_id=to[1],
                    message_id=to[2],
                    fail_if_not_exists=False,
                )
            elif (
                to == (reference.guild_id, reference.channel_id, reference.message_id)
                and de[1] == channel.id
            ):
                return discord.MessageReference(
                    guild_id=de[0], channel_id=de[1], message_id=de[2], fail_if_not_exists=False
                )
        return None

    async def process_kwargs(
        self,
        author: UnionUser,
        channel: UnionChannel,
        jump_url: str,
        *,
        content: Optional[str],
        attachments: List[discord.Attachment],
    ) -> Dict[str, Any]:
        guild: Optional[discord.Guild] = getattr(channel, "guild", None)
        oga = author
        if guild:
            author = guild.get_member(author.id) or self.bot.get_user(author.id)
            assert author
        if not await self.bot.allowed_by_whitelist_blacklist(author):
            raise RiftError(_("You are not permitted to use the bot here."))
        is_owner = await self.bot.is_owner(author)
        me = (guild or channel).me  # type: ignore
        author_perms = self.permissions(channel, author, is_owner)
        bot_perms = self.permissions(channel, me)
        both_perms = discord.Permissions(author_perms.value & bot_perms.value)
        if guild and content and not is_owner and not await self.bot.is_automod_immune(author):
            assert isinstance(channel, discord.TextChannel)
            filt: Optional["Filter"] = self.bot.get_cog("Filter")  # type: ignore
            if filt and await filt.filter_hits(content, channel):
                raise RiftError(_("Your message was filtered."))
        embed: Optional[List[discord.Embed]]
        if await self.bot.embed_requested(
            getattr(channel, "recipient", channel), command=self.rift  # type: ignore
        ):
            embed = [
                discord.Embed(
                    colour=oga.colour or await self.bot.get_embed_color(channel), url=jump_url
                )
            ]
            ogg: Optional[discord.Guild]
            if ogg := getattr(oga, "guild", None):
                assert isinstance(oga, discord.Member)
                if oga.top_role != ogg.default_role:
                    embed[0].title = filter_invites(f"{oga.top_role} in {ogg}")
                else:
                    embed[0].title = filter_invites(f"in {ogg}")
            embed[0].set_author(
                name=filter_invites(str(author)),
                icon_url=oga.display_avatar.replace(size=32).url,
            )
        else:
            content = f"{author}\n{quote(content)}" if content else str(author)
            embed = None
        if attachments and author_perms.attach_files:
            if embed:
                embed = self.get_embeds(attachments, embed=embed[0])
            else:
                if content:
                    content = f"{content}\n\n{_('Attachments:')}\n"
                else:
                    content = _("Attachments:")
                content += "\n".join(
                    f"[\N{PAPERCLIP}`{a.filename}`](<{a.url}>) ({self.xbytes(a.size)})"
                    for a in attachments
                )
        if not content and not embed:
            raise RiftError(_("Nothing to send."))
        if is_owner:
            allowed_mentions = discord.AllowedMentions.all()
        else:
            if content and not both_perms.administrator:
                content = filter_invites(content)
            if both_perms.mention_everyone:
                allowed_mentions = discord.AllowedMentions.all()
            else:
                allowed_mentions = discord.AllowedMentions(users=True)
        return {"allowed_mentions": allowed_mentions, "embeds": embed, "content": content}

    async def try_or_remove(self, coro: Awaitable[T], channel: UnionChannel) -> T:
        guild: Optional[discord.Guild] = getattr(channel, "guild", None)
        if guild:
            me = guild.me
        else:
            me = channel.me  # type: ignore
        vertex: Messageable = getattr(channel, "recipient", channel)
        try:
            return await coro
        except discord.Forbidden:
            if not guild or not channel.permissions_for(me).send_messages:
                # we can't send here anymore, may as well remove it
                self.rifts.remove_vertices(vertex)
            raise

    # EVENTS

    @commands.Cog.listener()
    async def on_typing(self, channel: "Messageable", user: UnionUser, when: datetime):
        if user.bot:
            return
        destinations = deduplicate_iterables(
            self.rifts.get(Limited(author=user, channel=channel), ()), self.rifts.get(channel, ())
        )
        await asyncio.gather(
            *(channel.typing() for channel in destinations), return_exceptions=True
        )

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.author.bot:
            return
        if message.type != discord.MessageType.default:
            return
        if not message.content and not message.attachments:
            return
        if not await self.bot.message_eligible_as_command(message):
            return
        channel = message.channel if message.guild else message.author
        destinations = deduplicate_iterables(
            self.rifts.get(Limited(message=message), ()), self.rifts.get(channel, ())
        )
        if not destinations:
            return
        if message.content.casefold() == "exit":
            if await can_close(message, self.bot):
                if num := await self.close_rifts(message.author, channel):
                    return await message.channel.send(_("{num} rifts closed.").format(num=num))
            else:
                if num := await self.close_rifts(message.author, Limited(message=message)):
                    return await message.channel.send(_("{num} rifts closed.").format(num=num))
        await self._send(message, destinations)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if "author" not in payload.data or payload.data["author"].get("bot", False):
            return
        process = partial(self.process_edit, payload)
        await asyncio.gather(
            *map(
                process,
                filter(
                    None,
                    (
                        self._partial(guild_id, channel_id, message_id)
                        for guild_id, channel_id, message_id in self.messages.get(
                            (payload.guild_id, payload.channel_id, payload.message_id), ()
                        )
                    ),
                ),
            ),
            return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.cached_message and payload.cached_message.author.bot:
            return
        await asyncio.gather(
            *map(
                discord.PartialMessage.delete,
                filter(
                    None,
                    (
                        self._partial(guild_id, channel_id, message_id)
                        for guild_id, channel_id, message_id in self.messages.pop(
                            (payload.guild_id, payload.channel_id, payload.message_id), ()
                        )
                    ),
                ),
            ),
            return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        to_delete: Dict[discord.abc.Messageable, List[int]] = {}
        for pmid in payload.message_ids:
            for ids in self.messages.pop((payload.guild_id, payload.channel_id, pmid), ()):
                if partial_message := self._partial(*ids):
                    to_delete.setdefault(partial_message.channel, []).append(partial_message.id)
        await asyncio.gather(
            *starmap(partial(purge_ids, now=datetime.now(timezone.utc)), to_delete.items())
        )
