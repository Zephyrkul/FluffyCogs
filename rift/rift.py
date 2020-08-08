import asyncio
import logging
from itertools import chain
from traceback import walk_tb
from types import SimpleNamespace
from typing import TYPE_CHECKING, List, Literal, Optional, Set, Union, overload

import discord
from redbot.core import Config, checks, commands
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils import common_filters, deduplicate_iterables, mod
from redbot.core.utils.chat_formatting import humanize_list, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.predicates import MessagePredicate

from .graph import SimpleGraph, Vector, WeakKeyGraph

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

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.rifts = SimpleGraph[Messageable]()
        self.messages = WeakKeyGraph[discord.Message]()
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
        """
        if not rifts:
            raise commands.UserInputError()
        unique_rifts = deduplicate_iterables(rifts)
        await ctx.send("What would you like to say?")
        p = MessagePredicate.same_context(ctx=ctx)
        message = ctx.bot.wait_for("message", check=p)
        await self._send(message, unique_rifts)

    @commands.group()
    async def rift(self, ctx: commands.Context):
        """
        Communicate with other channels through Red.
        """

    @rift.group(aliases=["blocklist", "blacklist"])
    @check_can_close()
    async def denylist(self, ctx: commands.Context):
        """
        Configures denylists.

        Denylisted destinations cannot have rifts opened to them.
        """

    @denylist.command(name="channel")
    @check_can_close()
    async def denylist_channel(
        self, ctx: commands.Context, *, channel: discord.TextChannel = None
    ):
        """
        Denylists the current channel or the specified channel.

        Can also denylist DM channels.
        """
        if channel and not ctx.guild:
            raise commands.BadArgument(_("You cannot denylist a channel in DMs."))
        if not ctx.guild:
            channel = ctx.author
            group = self.config.user(channel)
        else:
            channel = channel or ctx.channel
            group = self.config.channel(channel)
        blacklisted = not await group.blacklisted()
        await group.blacklisted.set(blacklisted)
        await ctx.send(
            _("Channel is {} denylisted.").format("now" if blacklisted else "no longer")
        )
        if blacklisted:
            self.close_rifts(ctx.author, channel)

    @denylist.command(name="server", aliases=["guild"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def denylistlist_server(self, ctx: commands.Context):
        """
        Denylists the current server.

        All channels and members in a server are considered denylisted if the server is denylisted.
        Members can still be reached if they are in another, non-denylisted server.
        """
        group = self.config.guild(ctx.guild)
        blacklisted = not await group.blacklisted()
        await group.blacklisted.set(blacklisted)
        await ctx.send(_("Server is {} denylisted.").format("now" if blacklisted else "no longer"))
        if blacklisted:
            self.close_rifts(ctx.author, *ctx.guild.text_channels)

    @rift.group(name="close", invoke_without_command=True)
    async def rift_close(self, ctx: commands.Context):
        """
        Closes all rifts that lead to this channel.
        """
        channel = ctx.channel if ctx.guild else ctx.author
        if await can_close(ctx):
            num = self.close_rifts(ctx.author, channel)
        else:
            num = self.close_rifts(ctx.author, Limited(message=ctx.message))
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
        num = self.close_rifts(ctx.author, *ctx.guild.text_channels)
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

    @rift.command(name="link")
    @check_can_close()
    async def rift_link(
        self, ctx: commands.Context, one_way: Optional[bool] = None, *rifts: Messageable
    ):
        """
        Opens a rift to the specified destination(s).
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
                "A rift has been opened to {}! Everything you say will be relayed there.\n"
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
        unique_rifts: List[Messageable] = deduplicate_iterables(self.maybe_chain(rifts))
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
        if allowed_types is None:
            allowed_types = ["users"]
        if "users" in allowed_types and allowed_users:
            raise ValueError("Invalid configuration")
        if "roles" in allowed_types and allowed_roles:
            raise ValueError("Invalid configuration")
        if discord.version_info >= (1, 4):
            mentions = discord.AllowedMentions(
                everyone="everyone" in allowed_types,
                users="users" in allowed_types or allowed_users,
                roles="roles" in allowed_types or allowed_roles,
            )
            if isinstance(destination, discord.Message):
                await destination.edit(content=content, embed=embed, allowed_mentions=mentions)
                return destination
            return await destination.send(content=content, embed=embed, allowed_mentions=mentions)
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
                await channel.send(f"I couldn't send your message to {destination}: {reason}")
            else:
                self.messages.add_vectors(message, m)

    def close_rifts(self, closer: discord.abc.User, *destinations: Messageable):
        unique = set()
        for destination in destinations:
            unique.add(destination)
            if not isinstance(destination, (Limited, discord.abc.User)):
                unique.add(Limited(author=closer, channel=destination))
        fmt = _("{closer} has closed a rift to here from {source}.")

        processed: Set[Vector[Messageable]] = set()
        num_closed = 0
        for source, dest in self.rifts.vectors():
            if (dest, source) in processed:
                continue
            if source in unique:
                asyncio.ensure_future(dest.send(fmt.format(closer=closer, source=source)))
                num_closed += 1
            elif dest in unique:
                asyncio.ensure_future(source.send(fmt.format(closer=closer, source=dest)))
                num_closed += 1
            processed.add((source, dest))

        self.rifts.remove_vertices(*unique)
        return num_closed

    async def get_embed(self, destination, attachments):
        if not attachments:
            return
        embed = discord.Embed(colour=await self.bot.get_embed_color(destination))
        for a in attachments:
            embed.add_field(
                name=self.xbytes(a.size), value=f"[{a.filename}]({a.url})", inline=True
            )
        embed.set_image(url=attachments[0].url)
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
                if num := self.close_rifts(message.author, channel):
                    return await message.channel.send(_("{num} rifts closed.").format(num=num))
            else:
                if num := self.close_rifts(message.author, Limited(message=message)):
                    return await message.channel.send(_("{num} rifts closed.").format(num=num))
        await self._send(message, destinations)

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
            *(self.process_discord_message(message, m) for m in self.messages.get(message, ())),
            return_exceptions=True,
        )
