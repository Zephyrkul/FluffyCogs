import asyncio
import logging
from functools import partial
from io import BytesIO
from traceback import walk_tb
from types import SimpleNamespace
from typing import TYPE_CHECKING, List, Optional, Set, Tuple, Union, overload

import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils import common_filters, deduplicate_iterables, mod
from redbot.core.utils.chat_formatting import humanize_list, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.predicates import MessagePredicate

from .graph import SimpleGraph, Vector

if TYPE_CHECKING:
    from discord.abc import Messageable
    from redbot.cogs.filter import Filter
else:
    from .converter import DiscordConverter as Messageable

from .converter import Limited

log = logging.getLogger("red.fluffy.rift")
_ = Translator(__name__, __file__)


@overload
async def can_close(ctx: commands.Context) -> bool:
    ...


@overload
async def can_close(ctx: discord.Message, bot) -> bool:
    ...


async def can_close(ctx: Union[commands.Context, discord.Message], bot=None):
    """Admin / manage channel OR private channel"""
    if isinstance(ctx.channel, discord.DMChannel):
        return True
    """
    if ctx.bot.get_cog(Rift.__name__).graph.is_allowed(
        ctx.channel if ctx.guild else ctx.author, user=ctx.author, strict=True
    ):
        return True
    """
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
        self.messages = SimpleGraph[Tuple[int, int]]()
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_channel(blacklisted=False)
        self.config.register_guild(blacklisted=False)
        self.config.register_user(blacklisted=False)
        self.config.register_global(
            notify=True,  # format="[{role}] {author}", format_no_guild="{author}"
        )
        self._cache_invalidator.start()

    @tasks.loop(minutes=5)
    async def _cache_invalidator(self):
        # longer-running bots need to remove expired cached messages
        oldest_id = self.bot.cached_messages[0].id
        for channel_id, message_id in list(
            self.messages.keys()
        ):  # iterate over a copy, not a view
            if message_id < oldest_id:
                self.messages.pop((channel_id, message_id))
            else:
                # dicts are ordered in Python
                break

    @_cache_invalidator.before_loop
    async def _before_cache(self):
        await self.bot.wait_until_ready()
        # I can't be arsed to check for IndexErrors
        await self.bot.wait_for("message")

    def cog_unload(self):
        self._cache_invalidator.cancel()

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

    @rift.command(name="open")
    async def rift_open(
        self, ctx: commands.Context, one_way: Optional[bool] = None, *rifts: Messageable
    ):
        """
        Opens a rift to the specified destination(s).

        Only your messages will be forwarded to the specified destinations,
        and all replies will be sent back to you.
        """
        if not rifts:
            raise commands.UserInputError()
        unique_rifts: List[Messageable] = deduplicate_iterables(rifts)
        source = Limited(message=ctx.message) if ctx.guild else ctx.author
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        for rift in unique_rifts:
            if (
                no_notify
                and getattr(rift, "guild", None)
                and not isinstance(rift, discord.abc.User)
            ):
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
                        _("{} has opened a rift to here from {}.").format(ctx.author, ctx.channel)
                    )
                )
        await ctx.send(
            _(
                "A rift has been opened to {}! Everything you say will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, unique_rifts))))
        )

    @rift.command(name="link")
    @check_can_close()
    async def rift_link(
        self, ctx: commands.Context, one_way: Optional[bool] = None, *rifts: Messageable
    ):
        """
        Links this channel to the specified destination(s).

        Anything anyone says in this channel will be forwarded.
        All replies will be relayed back here.
        """
        if not rifts:
            raise commands.UserInputError()
        unique_rifts: List[Messageable] = deduplicate_iterables(rifts)
        source = ctx.channel if ctx.guild else ctx.author
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        for rift in unique_rifts:
            if (
                no_notify
                and getattr(rift, "guild", None)
                and not isinstance(rift, discord.abc.User)
            ):
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
                        _("{} has linked a rift to here from {}.").format(ctx.author, ctx.channel)
                    )
                )
        await ctx.send(
            _(
                "A link has been created to {}! Everything said in this channel will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, unique_rifts))))
        )

    @rift.command(name="web")
    @checks.is_owner()
    async def rift_web(self, ctx: commands.Context, *rifts: Messageable):
        """
        Opens up all possible connections between this channel and the specified rifts.

        See the helptext of `[p]rift link` for more info.
        """
        if not rifts:
            raise commands.UserInputError()
        unique_rifts: List[Messageable] = deduplicate_iterables(rifts)
        source = ctx.channel if ctx.guild else ctx.author
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        self.rifts.add_web(source, *unique_rifts)
        humanized = humanize_list(list(map(str, (source, *unique_rifts))))
        for rift in unique_rifts:
            if no_notify and getattr(rift, "guild", None):
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
            )

        if not scoped and not await ctx.bot.is_owner(ctx.author):
            raise commands.CheckFailure()
        if scoped == ctx.guild and not await mod.is_admin_or_superior(ctx.bot, ctx.author):
            raise commands.CheckFailure()

        def check(vector):
            if not scoped:
                return True
            if scoped in vector:
                return True
            if scoped in map(lambda c: getattr(c, "guild", None), vector):
                return True
            return False

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

    async def _send(self, message, destinations):
        futures = [
            asyncio.ensure_future(self.process_discord_message(message, d)) for d in destinations
        ]
        if len(futures) == 1:
            fs = futures  # no need to wrap this up in as_completed
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
                self.messages.add_vectors((message.channel.id, message.id), (m.channel.id, m.id))

    async def close_rifts(self, closer: discord.abc.User, *destinations: Messageable):
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
                    asyncio.ensure_future(dest.send(fmt.format(closer=closer, source=source)))
                num_closed += 1
            elif dest in unique:
                if notify:
                    asyncio.ensure_future(source.send(fmt.format(closer=closer, source=dest)))
                num_closed += 1
            processed.add((source, dest))

        self.rifts.remove_vertices(*unique)
        return num_closed

    async def get_embed(self, destination, attachments):
        if not attachments:
            return
        attachment = attachments[0]
        embed = discord.Embed(colour=await self.bot.get_embed_color(destination))
        if attachment.url.lower().endswith(("png", "jpeg", "jpg", "gif", "webp")):
            embed.set_image(url=attachment.url)
        else:
            embed.add_field(
                name=self.xbytes(attachment.size),
                value=f"[{attachment.filename}]({attachment.url})",
                inline=True,
            )
        return embed

    def permissions(self, destination, user, is_owner=False):
        if destination.type == discord.ChannelType.private:
            return destination.permissions_for(self.bot.user)
        if not is_owner:
            member = destination.guild.get_member(user.id)
            if member:
                return destination.permissions_for(member)
            else:
                every = destination.guild.default_role
                overs = destination.overwrites_for(every)
                overs = overs.pair()
                perms = (every.permissions.value & ~overs[1].value) | overs[0].value
                log.debug(
                    "calculated permissions for @everyone in guild %s: %s",
                    destination.guild.id,
                    perms,
                )
                return discord.Permissions(perms)
        return discord.Permissions.all()

    @staticmethod
    def xbytes(b):
        blist = ("B", "KB", "MB")
        index = 0
        while True:
            if b > 900:
                b = b / 1024.0
                index += 1
            else:
                return "{:.3g} {}".format(b, blist[index])

    async def process_discord_message(self, message, destination):
        author = message.author
        if not await self.bot.allowed_by_whitelist_blacklist(author):
            return
        is_owner = await self.bot.is_owner(author)
        if isinstance(destination, discord.Message):
            channel = destination.channel
        elif method := getattr(destination, "create_dm", None):
            channel = await method()
        else:
            channel = destination
        guild = getattr(channel, "guild", None)
        me = (guild or channel).me
        if not is_owner and guild:
            dest_author = guild.get_member(author.id)
            if dest_author:
                is_automod_immune = await self.bot.is_automod_immune(dest_author)
            else:
                is_automod_immune = False
        else:
            is_automod_immune = True
        author_perms = self.permissions(channel, author, is_owner)
        bot_perms = self.permissions(channel, me)
        both_perms = discord.Permissions(author_perms.value & bot_perms.value)
        content = message.content
        if not is_automod_immune:
            filt: "Filter" = self.bot.get_cog("Filter")
            if filt and await filt.filter_hits(content, destination):
                raise RiftError("Your message was filtered at the destination.")
        attachments = message.attachments
        embed = None
        if attachments and author_perms.attach_files:
            if bot_perms.embed_links:
                embed = await self.get_embed(destination, attachments)
            else:
                if content:
                    content = f"{content}\n\n{_('Attachments:')}\n"
                else:
                    content = _("Attachments:")
                content += "\n".join(f"({self.xbytes(a.size)}) {a.url}" for a in attachments)
        if not content and not embed:
            raise RiftError(_("No content to send."))
        allowed_mentions = discord.AllowedMentions()
        if not is_owner:
            top_role = getattr(author, "top_role", None)
            if top_role and top_role != top_role.guild.default_role:
                content = f"[{author.top_role}] {author}\n>>> {content}"
            else:
                content = f"{author}\n>>> {content}"
            if not both_perms.administrator:
                content = common_filters.filter_invites(content)
            if not both_perms.mention_everyone:
                allowed_mentions = discord.AllowedMentions(users=True, everyone=True, roles=True)
            else:
                allowed_mentions = discord.AllowedMentions(users=True)
        try:
            if isinstance(destination, discord.Message):
                coro = destination.edit
            else:
                coro = destination.send
            return await coro(content=content, embed=embed, allowed_mentions=allowed_mentions)
        except discord.Forbidden:
            if not channel.permissions_for(me).send_messages:
                # we can't send here anymore, may as well remove it
                self.rifts.remove_vertices(getattr(channel, "recipient", channel))
            raise

    # EVENTS

    @commands.Cog.listener()
    async def on_message_without_command(self, message):
        if message.author.bot:
            return
        if not message.content and not message.attachments:
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
    async def on_message_delete(self, message):
        if message.author.bot:
            return
        return asyncio.gather(
            *map(
                discord.Message.delete,
                filter(
                    None,
                    (
                        discord.utils.get(
                            self.bot.cached_messages, id=message_id, channel__id=channel_id
                        )
                        for channel_id, message_id in self.messages.pop(
                            (message.channel.id, message.id), ()
                        )
                    ),
                ),
            ),
            return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, _b, message):
        if message.author.bot:
            return
        process = partial(self.process_discord_message, message)
        await asyncio.gather(
            *map(
                process,
                filter(
                    None,
                    (
                        discord.utils.get(
                            self.bot.cached_messages, id=message_id, channel__id=channel_id
                        )
                        for channel_id, message_id in self.messages.get(
                            (message.channel.id, message.id), ()
                        )
                    ),
                ),
            ),
            return_exceptions=True,
        )
