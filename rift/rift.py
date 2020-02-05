import asyncio
import logging
from contextlib import suppress
from typing import Dict, Union

import discord
import discordtextsanitizer as dts

from redbot.core import commands, checks, Config
from redbot.core.utils import common_filters, deduplicate_iterables, mod
from redbot.core.utils.chat_formatting import pagify, humanize_list
from redbot.core.i18n import Translator, cog_i18n

from .converter import URL, VertexConverter, search_converter
from .irc import RiftIRCClient, IRCMessageable
from .graph import Graph


log = logging.getLogger("red.fluffy.rift")
_ = Translator(__name__, __file__)


@commands.permissions_check
async def check_can_close(ctx):
    """Admin / manage channel OR private channel"""
    if isinstance(ctx.channel, discord.DMChannel):
        return True
    return await mod.is_admin_or_superior(ctx.bot, ctx.author) or await mod.check_permissions(
        ctx, {"manage_channels": True}
    )


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
        self.graph = Graph()
        self.irc_clients: Dict[URL, RiftIRCClient] = {}
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_channel(blacklisted=False)
        self.config.register_guild(blacklisted=False)
        self.config.register_user(blacklisted=False, ignore=False)
        self.config.register_member(ignore=False)
        self.config.register_global(notify=True)

    # COMMANDS

    @commands.group()
    async def rift(self, ctx):
        """
        Communicate with other channels through Red.
        """
        pass

    @rift.group()
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
        await ctx.maybe_send_embed(
            _("Channel is {} blacklisted.").format("now" if blacklisted else "no longer")
        )
        if blacklisted:
            await self.close_rifts(ctx, ctx.author, channel)

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
        await ctx.maybe_send_embed(
            _("Server is {} blacklisted.").format("now" if blacklisted else "no longer")
        )
        if blacklisted:
            await self.close_rifts(ctx, ctx.author, ctx.guild)

    @rift.command(name="close")
    @check_can_close
    async def rift_close(self, ctx):
        """
        Closes all rifts that lead to this channel.
        """
        channel = ctx.channel if ctx.guild else ctx.author
        await self.close_rifts(ctx, ctx.author, channel)

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

    @rift.group(name="ignore", invoke_without_command=True)
    @checks.admin_or_permissions(manage_roles=True)
    async def rift_ignore(self, ctx, *, user: discord.Member):
        """
        TODO
        """
        pass

    @rift_ignore.command(name="global")
    @checks.is_owner()
    async def ignore_global(self, ctx, *, user: discord.User):
        """
        TODO
        """
        pass

    @rift.command(name="irc")
    async def rift_irc(self, ctx, domain: URL, *channels: Union[discord.TextChannel, str]):
        channels = deduplicate_iterables(channels)
        client = self.irc_clients.get(domain, None)
        async with ctx.typing():
            if not client:
                log.debug("No client for domain %s found, creating...", domain)
                client = RiftIRCClient(bot=ctx.bot)
                self.irc_clients[domain] = client
                # for some reason it sends the connect call and does nothing about it
                asyncio.ensure_future(client.connect(domain, tls=True))
                await ctx.bot.wait_for("pydle_connect")
                log.debug("Client for %s connected? %s", domain, client.connected)
            else:
                log.debug("Using existing client for domain %s", domain)
            for i, channel in enumerate(channels):

                def check(cl, ch, us):
                    return cl.in_channel(ch)

                if isinstance(channel, discord.TextChannel):
                    channel = f"#{channel.name}"
                if client.is_channel(channel):
                    if not client.in_channel(channel):
                        log.debug("Joining channel %s...", channel)
                        asyncio.ensure_future(client.join(channel))
                        await ctx.bot.wait_for("pydle_join", check=check)
                        log.debug("Joined to %s? %s", channel, client.in_channel(channel))
                    else:
                        log.debug("Already in channel %s", channel, exc_info=True)
                irc_channel = client[channel]
                channels[i] = irc_channel
                self.graph.add_vectors(
                    ctx.channel if ctx.guild else ctx.author, irc_channel, two_way=True
                )
                asyncio.ensure_future(
                    irc_channel.send(_("{} has opened a rift to here.").format(ctx.author))
                )
        await ctx.send(
            _(
                "A rift has been opened to {}! Everything you say will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, channels))))
        )

    @rift.command(name="open")
    async def rift_open(self, ctx, *rifts: VertexConverter):
        """
        Opens a rift to the specified destination.

        The destination may be any channel or user that both you and the bot are connected to, even across servers.
        """
        if not rifts:
            return await ctx.send_help()
        unique_rifts = deduplicate_iterables(rifts)
        source = ctx.channel if ctx.guild else ctx.author
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        for rift in unique_rifts:
            if no_notify and rift.destination.guild:
                mem = rift.destination.guild.get_member(ctx.author.id)
                if mem and rift.destination.permissions_for(mem).read_messages:
                    notify = False
                else:
                    notify = True
            else:
                notify = True
            self.graph.add_vectors(source, rift, two_way=True)
            if notify:
                ctx.bot.loop.create_task(
                    rift.send(_("{} has opened a rift to here.").format(ctx.author))
                )
        await ctx.send(
            _(
                "A rift has been opened to {}! Everything you say will be relayed there.\n"
                "Responses will be relayed here.\n"
                "Type `exit` to quit."
            ).format(humanize_list(list(map(str, unique_rifts))))
        )

    @rift.command(name="search")
    async def rift_search(self, ctx, searchby: search_converter = None, *, search=None):
        # TODO
        """
        Searches through open rifts.

        searchby: author, source, or destination. If this isn't provided, all
        three are searched through.
        search: Search for the specified author/source/destination. If this
        isn't provided, the author or channel of the command is used.
        """
        searchby = searchby or list(range(3))
        if search is None:
            search = [ctx.author, ctx.channel, ctx.author]
        else:
            search = await VertexConverter.search(ctx, search, globally=False)
        results = set()
        for rift in self.open_rifts:
            for i in searchby:
                if rift[i] in search:
                    results.add(rift)
        if not results:
            return await ctx.maybe_send_embed(_("No rifts were found with these parameters."))
        message = _("Results:") + "\n\n"
        message += "\n".join(str(rift) for rift in results)
        for page in pagify(message):
            await ctx.maybe_send_embed(page)

    # SPECIAL METHODS

    def cog_unload(self):
        for client in self.irc_clients.values():
            self.bot.loop.create_task(client.disconnect())

    # UTILITIES

    async def close_rifts(self, ctx, closer, destination):
        # TODO: notify of close
        destinations = getattr(destination, "text_channels", [destination])
        self.graph.remove_vertices(*destinations)

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

    def permissions(self, destination, user, is_owner=False):
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

    def xbytes(self, b):
        blist = ("B", "KB", "MB")
        index = 0
        while True:
            if b > 900:
                b = b / 1024.0
                index += 1
            else:
                return "{:.3g} {}".format(b, blist[index])

    async def process_discord_message(self, message, destination):
        # TODO
        author = message.author
        is_owner = await self.bot.is_owner(author)
        if isinstance(destination, discord.Message):
            send_coro = destination.edit
            destination = destination.channel if destination.guild else destination.author
            log.debug("editing message %s-%s", destination.channel.id, destination.id)
        elif isinstance(destination, IRCMessageable):
            log.debug("sending message to irc channel %s", destination.name)
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
            send_coro = destination.send
            log.debug("sending message to channel %s", destination.id)
        me = destination.guild.me if destination.guild else destination.dm_channel.me
        author_perms = self.permissions(destination, author, is_owner)
        bot_perms = self.permissions(destination, me)
        content = message.content
        attachments = message.attachments
        embed = None
        if attachments and author_perms.attach_files:
            if bot_perms.embed_links:
                embed = await self.get_embed(destination, attachments)
            else:
                content = f"{content}\n\n{_('Attachments:')}\n"
                content += "\n".join(f"({self.xbytes(a.size)}) {a.url}" for a in attachments)
        if not content and not embed:
            raise RiftError(_("No content to send."))
        if not is_owner:
            content = f"{author}: {content}"
            if not author_perms.administrator:
                content = common_filters.filter_invites(content)
            if not author_perms.mention_everyone:
                content = common_filters.filter_mass_mentions(content)
        return await send_coro(content=content, embed=embed)

    # EVENTS

    @commands.Cog.listener()
    async def on_message_without_command(self, message):
        # TODO
        if message.author.bot:
            return
        channel = message.channel if message.guild else message.author
        if message.content.lower() == "exit":
            return self.graph.pop(channel, None)
        await asyncio.gather(
            *(
                self.process_discord_message(message, d)
                for d in self.graph.get(channel, ())
                # if self.graph.is_allowed(channel, d, user=message.author)
            ),
            return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.author.bot:
            return
        return asyncio.gather(
            *(m.delete() for m in self.graph.messages.pop(message, ())), return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, _b, message):
        if message.author.bot:
            return
        channel = message.channel if message.guild else message.author
        await asyncio.gather(
            *(
                self.process_discord_message(message, m)
                for m in self.graph.messages.get(message, ())
                # if self.graph.is_allowed(channel, m.channel if m.guild else m.author, user=message.author)
            ),
            return_exceptions=True,
        )

    @commands.Cog.listener()
    async def on_pydle_message(
        self, client: RiftIRCClient, channel: str, author: str, content: str
    ):
        if client.is_same_nick(author, client.nickname):
            return
        irc_channel = client[channel]
        prefix = ""
        with suppress(AttributeError):
            if author in irc_channel.modes.get("q", ()):
                prefix = "~"
            elif author in irc_channel.modes.get("a", ()):
                prefix = "&"
            elif author in irc_channel.modes.get("o", ()):
                prefix = "@"
            elif author in irc_channel.modes.get("h", ()):
                prefix = "%"
            elif author in irc_channel.modes.get("v", ()):
                prefix = "+"
        # *aggressively sanitizes*
        content = f"{prefix}{author}: {content}"
        content = dts.sanitize_mass_mentions(content, run_preprocess=True, users=True)
        content = common_filters.filter_invites(content)
        futures = (
            asyncio.ensure_future(destination.send(content))
            for destination in self.graph.get(irc_channel, ())
            # if self.graph.is_allowed(channel, destination, user=client[author])
        )
        for fut in asyncio.as_completed(futures):
            try:
                await fut
            except Exception:
                log.exception("Exception in task %r", asyncio.current_task())
