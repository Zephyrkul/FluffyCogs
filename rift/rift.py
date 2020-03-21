import asyncio
import discord
import logging
import re
from copy import copy
from contextlib import suppress
from itertools import chain
from operator import attrgetter
from traceback import walk_tb
from types import SimpleNamespace
from typing import TYPE_CHECKING, Dict, Set, List, Literal, Optional, Union, overload

from redbot.core import commands, checks, Config
from redbot.core.utils import common_filters, deduplicate_iterables, mod
from redbot.core.utils.chat_formatting import pagify, humanize_list
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.i18n import Translator, cog_i18n

from .irc import RiftIRCClient, IRCMessageable, IRCMessage
from .graph import SimpleGraph, Vector

if TYPE_CHECKING:
    URL = str
    IRCConverter = IRCMessageable
    from discord.abc import Messageable as DiscordConverter
    from redbot.cogs.filter import Filter
else:
    from .converter import URL, DiscordConverter, IRCConverter


Destination = Union[IRCConverter, DiscordConverter]

log = logging.getLogger("red.fluffy.rift")
_ = Translator(__name__, __file__)

mention_re = re.compile(r"@([^@#:]{2,32})(?:#(\d{4}))?")


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

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.rifts = SimpleGraph[Destination]()
        self.messages = SimpleGraph[discord.Message]()
        self.irc_clients: Dict[URL, RiftIRCClient] = {}
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_channel(blacklisted=False)
        self.config.register_guild(blacklisted=False)
        self.config.register_user(blacklisted=False)
        self.config.register_global(
            notify=True,  # format="[{role}] {author}", format_no_guild="{author}"
        )

    # COMMANDS

    @commands.group()
    async def rift(self, ctx):
        """
        Communicate with other channels through Red.
        """
        pass

    @rift.group()
    @check_can_close
    async def blacklist(self, ctx):
        """
        Configures blacklists.

        Blacklisted destinations cannot have rifts opened to them.
        """
        pass

    @blacklist.command(name="channel")
    @check_can_close
    async def blacklist_channel(self, ctx, *, channel: discord.TextChannel = None):
        """
        Blacklists the current channel or the specified channel.

        Can also blacklist DM channels.
        """
        if channel and not ctx.guild:
            raise commands.BadArgument(_("You cannot blacklist a channel in DMs."))
        if not ctx.guild:
            channel = ctx.author
            group = self.config.user(channel)
        else:
            channel = channel or ctx.channel
            group = self.config.channel(channel)
        blacklisted = not await group.blacklisted()
        await group.blacklisted.set(blacklisted)
        await ctx.send(
            _("Channel is {} blacklisted.").format("now" if blacklisted else "no longer")
        )
        if blacklisted:
            self.close_rifts(ctx.author, channel)

    @blacklist.command(name="server", aliases=["guild"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def blacklist_server(self, ctx):
        """
        Blacklists the current server.

        All channels and members in a server are considered blacklisted if the server is blacklisted.
        Members can still be reached if they are in another, non-blacklisted server.
        """
        group = self.config.guild(ctx.guild)
        blacklisted = not await group.blacklisted()
        await group.blacklisted.set(blacklisted)
        await ctx.send(
            _("Server is {} blacklisted.").format("now" if blacklisted else "no longer")
        )
        if blacklisted:
            self.close_rifts(ctx.author, *ctx.guild.text_channels)

    @rift.group(name="close", invoke_without_command=True)
    @check_can_close
    async def rift_close(self, ctx):
        """
        Closes all rifts that lead to this channel.
        """
        channel = ctx.channel if ctx.guild else ctx.author
        self.close_rifts(ctx.author, channel)
        await ctx.tick()

    @rift_close.command(name="guild", aliases=["server"])
    @commands.guild_only()
    @check_can_close
    async def close_guild(self, ctx):
        """
        Closes all rifts that lead to this server.
        """
        self.close_rifts(ctx.author, *ctx.guild.text_channels)
        await ctx.tick()

    @rift.command(name="notify")
    @checks.is_owner()
    async def rift_notify(self, ctx, *, notify: bool = None):
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

    @rift.command(name="link")
    @check_can_close
    async def rift_link(self, ctx, one_way: Optional[bool] = None, *rifts: Destination):
        """
        Opens a rift to the specified destination(s).

        The destination may be any channel or user that both you and the bot are connected to, even across servers.
        IRC destinations are also supported, with a supplied domain.
        For instance, `Zephyrkul@irc.freenode.net`, or `irc.freenode.net#python`
        Multiple destinations for one domain can be supplied, e.g. `Zephyrkul,Twentysix@irc.freenode.net##linux,python`
        """
        if not rifts:
            raise commands.UserInputError()
        unique_rifts: List[Destination] = deduplicate_iterables(self.maybe_chain(rifts))
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
                "A rift has been opened to {}! Everything you say will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, unique_rifts))))
        )

    @rift.command(name="web")
    @checks.is_owner()
    async def rift_web(self, ctx, *rifts: Destination):
        """
        Opens up all possible connections between this channel and the specified rifts.

        See the helptext of `[p]rift link` for more info.
        """
        if not rifts:
            raise commands.UserInputError()
        unique_rifts: List[Destination] = deduplicate_iterables(self.maybe_chain(rifts))
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
    async def rift_search(self, ctx, *, scope: str = "channel"):
        """
        Provides info about rifts opened in the specified scope.
        """
        try:
            scoped = dict(
                user=ctx.author,
                member=ctx.author,
                author=ctx.author,
                channel=ctx.channel if ctx.guild else ctx.author,
                guild=ctx.guild,
                server=ctx.guild,
                **{"global": None},
            )[scope.casefold()]
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

        unique_rifts: Set[Vector[Destination]] = set()
        for source, destination in chain(self.rifts.vectors(), self.user_rifts.vectors()):
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

    def cog_unload(self):
        for client in self.irc_clients.values():
            asyncio.ensure_future(client.disconnect())

    # UTILITIES

    @staticmethod
    def maybe_chain(iterable):
        for i in iterable:
            try:
                yield from i
            except TypeError:
                yield i

    # Temporary patch until d.py is updated to support the new allowed_mentions feature
    @staticmethod
    async def clean_send(
        destination: Union[discord.abc.Messageable, discord.Message],
        content: str = None,
        *,
        embed: discord.Embed = None,
        allowed_types: List[Literal["roles", "users", "everyone"]] = None,
        allowed_users: List[discord.User] = None,
        allowed_roles: List[discord.Role] = None,
    ) -> discord.Message:
        allowed_types = ["users"] if allowed_types is None else [str(t) for t in allowed_types]
        if "users" in allowed_types and allowed_users:
            raise ValueError("Invalid configuration")
        if "roles" in allowed_types and allowed_roles:
            raise ValueError("Invalid configuration")
        payload: dict = {"allowed_mentions": {"parse": allowed_types}}
        if allowed_users:
            payload["allowed_mentions"]["users"] = [str(u.id) for u in allowed_users]
        if allowed_roles:
            payload["allowed_mentions"]["roles"] = [str(r.id) for r in allowed_roles]
        if content:
            payload["content"] = content
        if embed:
            payload["embed"] = embed.to_dict()
        if isinstance(destination, discord.Message):
            raw_channel = destination.channel
            route = discord.http.Route(
                "PATCH",
                "/channels/{channel_id}/messages/{message_id}",
                channel_id=raw_channel.id,
                message_id=destination.id,
            )
            data = await destination._state.http.request(route, json=payload)
            destination._update(data)
            return destination
        else:
            raw_channel = await destination._get_channel()
            route = discord.http.Route(
                "POST", "/channels/{channel_id}/messages", channel_id=raw_channel.id
            )
            data = await destination._state.http.request(route, json=payload)
            return destination._state.create_message(channel=destination, data=data)

    def close_rifts(self, closer: Destination, *destinations: Union[discord.Guild, Destination]):
        unique = set(destinations)
        fmt = _("{closer} has closed a rift to here from {source}.")

        processed: Set[Vector[Destination]] = set()
        for source, dest in self.rifts.vectors():
            if (dest, source) in processed:
                continue
            if source in unique:
                asyncio.ensure_future(dest.send(fmt.format(closer=closer, source=source)))
            elif dest in unique:
                asyncio.ensure_future(source.send(fmt.format(closer=closer, source=dest)))
            processed.add((source, dest))

        self.rifts.remove_vertices(*unique)

    async def get_embed(self, destination, attachments):
        if not attachments:
            return
        embed = discord.Embed(colour=await self.bot.get_embed_color(destination))
        for a in attachments:
            embed.add_field(
                name=self.xbytes(a.size), value=f"[{a.filename}]({a.url})", inline=True
            )
        embed.set_image(url=attachments[0].url)
        embed._video = {"url": attachments[0].url}
        return embed

    @staticmethod
    def permissions(destination, user, is_owner=False):
        if isinstance(destination, discord.User):
            return destination.dm_channel.permissions_for(user)
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
        elif isinstance(destination, IRCMessageable):
            content = message.clean_content
            if message.attachments:
                content = f"{content}\n\n{_('Attachments:')}\n"
                content += "\n".join(
                    f"({self.xbytes(a.size)}) {a.url}" for a in message.attachments
                )
            if not is_owner:
                content = common_filters.filter_invites(f"{author.name}: {content}")
            return await destination.send(content)
        else:
            channel = getattr(destination, "dm_channel", destination)
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
        author_perms = self.permissions(destination, author, is_owner)
        bot_perms = self.permissions(destination, me)
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
        allowed_types = ["everyone", "roles", "users"]
        if not is_owner:
            top_role = getattr(author, "top_role", None)
            if top_role and top_role != top_role.guild.default_role:
                content = f"[{author.top_role}] {author}\n>>> {content}"
            else:
                content = f"{author}\n>>> {content}"
            if not both_perms.administrator:
                content = common_filters.filter_invites(content)
            if not both_perms.mention_everyone:
                allowed_types = ["user"]
        return await self.clean_send(destination, content=content, embed=embed)

    async def process_irc_message(
        self,
        prefix: str,
        message: IRCMessage,
        destination: Destination,
        *,
        is_op: bool = False,
        has_everyone: bool = False,
    ):
        if isinstance(destination, IRCMessageable):
            return await destination.send(f"{prefix}: {message.content}")

        filt: "Filter" = self.bot.get_cog("Filter")
        if filt and await filt.filter_hits(message.content, destination):
            raise RiftError("Your message was filtered at the destination.")

        if is_op and has_everyone:
            allowed_types = ["everyone"]
        else:
            allowed_types = []
        allowed_roles = []
        allowed_users = []

        def sub(m) -> str:
            if not m.group(2):
                group = m.group(1)
                role, longest = None, 0
                for r in reversed(destination.guild.roles):
                    rl = len(r.name)
                    if rl > longest and group.startswith(r.name):
                        role, longest = r, rl
                if role:
                    if is_op:
                        allowed_roles.append(role)
                    return role.mention + group[len(role.name) :]
            else:
                user = discord.utils.get(
                    destination.members, name=m.group(1), discriminator=m.group(2)
                )
                if user:
                    allowed_users.append(user)
                    return user.mention
            return m.group(0)

        if not getattr(destination, "members", None):
            if (mention := getattr(destination, "mention", None)) :
                content = message.content.replace(f"@{destination}", mention)
        else:
            content = mention_re.sub(sub, message.content)

        await self.clean_send(
            destination,
            f"{prefix}\n>>> {content}",
            allowed_types=allowed_types,
            allowed_roles=allowed_roles,
            allowed_users=allowed_users,
        )

    # EVENTS

    @commands.Cog.listener()
    async def on_message_without_command(self, message):
        if message.author.bot:
            return
        if not message.content and not message.attachments:
            return
        channel = message.channel if message.guild else message.author
        if message.content.casefold() == "exit" and await can_close(message, self.bot):
            self.close_rifts(message.author, channel)
            return await message.channel.send(_("Rift closed."))
        futures = [
            asyncio.ensure_future(self.process_discord_message(message, d))
            for d in self.rifts.get(channel, ())
            # if self.rifts.is_allowed(channel, d, user=message.author)
        ]
        if not futures:
            return
        for fut in asyncio.as_completed(futures):
            try:
                m = await fut
            except Exception as exc:
                for fr, _line in walk_tb(exc.__traceback__):
                    pass
                destination = fr.f_locals["destination"]
                log.exception(f"Couldn't send {message.id} to {destination}.")
                if isinstance(exc, (RiftError, discord.HTTPException)):
                    reason = " ".join(exc.args)
                else:
                    reason = f"{type(exc).__name__}. Check your console or logs for details."
                await channel.send(f"I couldn't send your message to {destination}: {reason}")
            else:
                self.messages.add_vectors(message, m)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.author.bot:
            return
        return asyncio.gather(
            *(m.delete() for m in self.messages.pop(message, ())), return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, _b, message):
        if message.author.bot:
            return
        channel = message.channel if message.guild else message.author
        await asyncio.gather(
            *(
                self.process_discord_message(message, m)
                for m in self.messages.get(message, ())
                # if self.rifts.is_allowed(channel, m.channel if m.guild else m.author, user=message.author)
            ),
            return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_pydle_message(self, client: RiftIRCClient, target: str, by: str, message: str):
        if client.is_same_nick(by, client.nickname):
            return
        if client.is_same_nick(target, client.nickname):
            irc_author = client[by]
            irc_channel = irc_author
        else:
            irc_author, irc_channel = client[by], client[target]
        if irc_channel.is_channel():
            is_op, prefix = False, ""
            with suppress(AttributeError):
                if by in irc_channel.modes.get("q", ()):
                    is_op, prefix = True, "~"
                elif by in irc_channel.modes.get("a", ()):
                    is_op, prefix = True, "&"
                elif by in irc_channel.modes.get("o", ()):
                    is_op, prefix = True, "@"
                elif by in irc_channel.modes.get("h", ()):
                    prefix = "%"
                elif by in irc_channel.modes.get("v", ()):
                    prefix = "+"
        else:
            is_op, prefix = True, ""
        if message.casefold() == "exit" and is_op:
            self.close_rifts(irc_author, irc_channel)
            return await irc_channel.send(_("Rift closed."))
        has_everyone = "@everyone" in message
        prefix = common_filters.filter_invites(f"{prefix}{irc_author.name}")
        message = common_filters.filter_invites(message)
        irc_message = IRCMessage(client, message, irc_author, irc_channel)
        futures = [
            asyncio.ensure_future(
                self.process_irc_message(
                    prefix, irc_message, destination, is_op=is_op, has_everyone=has_everyone
                )
            )
            for destination in self.rifts.get(irc_channel, ())
            # if self.rifts.is_allowed(irc_channel, destination, user=irc_author)
        ]
        if not futures:
            return
        for fut in asyncio.as_completed(futures):
            try:
                await fut
            except Exception as exc:
                for fr, _ in walk_tb(exc.__traceback__):
                    pass
                destination = fr.f_locals["destination"]
                log.exception(f"Couldn't send message to {destination}.")
                if isinstance(exc, (RiftError, discord.HTTPException)):
                    reason = " ".join(exc.args)
                else:
                    reason = f"{type(exc).__name__}. Check your console or logs for details."
                await irc_channel.send(f"I couldn't send your message to {destination}: {reason}")

    """
    @commands.Cog.listener()
    async def on_pydle_unknown(self, client, message):
        pass
    """
