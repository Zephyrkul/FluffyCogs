import asyncio
from contextlib import suppress
from copy import copy
from io import BytesIO

import discord

from redbot.core import commands, checks, Config
from redbot.core.utils import common_filters, mod
from redbot.core.utils.chat_formatting import pagify, humanize_list
from redbot.core.i18n import Translator, cog_i18n

check_permissions = getattr(mod, "check_permissions", checks.check_permissions)

from .converter import RiftConverter, search_converter


Cog = getattr(commands, "Cog", object)
listener = getattr(Cog, "listener", lambda: lambda x: x)


_ = Translator("Rift", __file__)


max_size = 8_000_000  # can be 1 << 23 but some unknowns also add to the size


async def close_check(ctx):
    """Admin / manage channel OR private channel"""
    if isinstance(ctx.channel, discord.DMChannel):
        return True
    return await mod.is_admin_or_superior(ctx.bot, ctx.author) or await check_permissions(
        ctx, {"manage_channels": True}
    )


class RiftError(Exception):
    pass


class Rift(Cog):
    """
    Communicate with other servers/channels.
    """

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.open_rifts = {}

        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_channel(blacklisted=False)
        self.config.register_guild(blacklisted=False)
        self.config.register_user(blacklisted=False)
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
    @commands.check(close_check)
    async def blacklist_channel(self, ctx, *, channel: discord.TextChannel = None):
        """
        Blacklists the current channel or the specified channel.

        Can also blacklist DM channels.
        """
        if channel and isinstance(ctx.channel, discord.DMChannel):
            raise commands.BadArgument(_("You cannot blacklist a channel in DMs."))
        if isinstance(ctx.channel, discord.DMChannel):
            channel = ctx.author
            group = self.config.user(channel)
        else:
            channel = channel or ctx.channel
            group = self.config.channel(channel)
        blacklisted = not await group.blacklisted()
        await group.blacklisted.set(blacklisted)
        await ctx.maybe_send_embed(
            _("Channel is {} blacklisted.".format("now" if blacklisted else "no longer"))
        )
        if blacklisted:
            await self.close_rifts(ctx, ctx.author, channel)

    @blacklist.command(name="server")
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
            _("Server is {} blacklisted.".format("now" if blacklisted else "no longer"))
        )
        if blacklisted:
            await self.close_rifts(ctx, ctx.author, ctx.guild)

    @rift.command(name="close")
    @commands.check(close_check)
    async def rift_close(self, ctx):
        """
        Closes all rifts that lead to this channel.
        """
        channel = ctx.author if isinstance(ctx.channel, discord.DMChannel) else ctx.channel
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
            _(
                "I will {} notify destinations when you open new rifts.".format(
                    "now" if notify else "no longer"
                )
            )
        )

    @rift.command(name="open")
    async def rift_open(self, ctx, *rifts: RiftConverter(_, globally=True)):
        """
        Opens a rift to the specified destination.

        The destination may be any channel or user that both you and the bot are connected to, even across servers.
        """
        if not rifts:
            return await ctx.send_help()
        rifts = set(rifts)
        no_notify = await self.bot.is_owner(ctx.author) and not await self.config.notify()
        for rift in rifts:
            if no_notify and isinstance(rift.destination, discord.abc.GuildChannel):
                mem = rift.destination.guild.get_member(ctx.author.id)
                if mem and rift.destination.permissions_for(mem).read_messages:
                    notify = False
                else:
                    notify = True
            else:
                notify = True
            self.open_rifts[rift] = {"notify": notify}
            if notify:
                ctx.bot.loop.create_task(
                    rift.destination.send(_("{} has opened a rift to here.").format(rift.author))
                )
        await ctx.send(
            _(
                "A rift has been opened to {}! Everything you say will be relayed there.\nResponses will be relayed here.\nType `exit` to quit."
            ).format(humanize_list([str(rift.destination) for rift in rifts]))
        )

    @rift.command(name="search")
    async def rift_search(self, ctx, searchby: search_converter(_) = None, *, search=None):
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
            search = await RiftConverter.search(ctx, search, False, _)
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

    # UTILITIES

    async def close_rifts(self, ctx, closer, destination):
        if isinstance(destination, discord.Guild):
            check = lambda rift: rift.destination in destination.channels
        else:
            check = lambda rift: rift.destination == destination
        noclose = True
        for rift in self.open_rifts.copy():
            if check(rift):
                del self.open_rifts[rift]
                noclose = False
                await rift.source.send(
                    _("{} has closed the rift to {}.").format(closer, rift.destination)
                )
                await rift.destination.send(_("Rift from {} closed.").format(rift.source))
        if noclose:
            await ctx.send(_("No rifts were found that connect to here."))

    async def get_embed(self, destination, attachments):
        attach = attachments[0]
        if (
            hasattr(destination, "guild")
            and await self.bot.db.guild(destination.guild).use_bot_color()
        ):
            color = destination.guild.me.colour
        else:
            color = self.bot.color
        description = "\n\n".join(
            f"{self.xbytes(attach.size)}\n**[{attach.filename}]({attach.url})**"
            for a in attachments
        )
        embed = discord.Embed(colour=color, description=description)
        embed.set_image(url=attach.url)
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
                overs.read_messages = True
                overs.send_messages = True
                overs = overs.pair()
                perms = (every.permissions.value & ~overs[1].value) | overs[0].value
                return discord.Permissions(perms)
        return discord.Permissions.all()

    async def process_message(self, rift, message, destination):
        if isinstance(destination, discord.Message):
            send_coro = destination.edit
        else:
            send_coro = destination.send
        channel = (
            message.author if isinstance(message.channel, discord.DMChannel) else message.channel
        )
        send = channel == rift.source
        destination = rift.destination if send else rift.source
        author = message.author
        me = (
            destination.dm_channel.me
            if isinstance(destination, discord.User)
            else destination.guild.me
        )
        is_owner = await self.bot.is_owner(author)
        author_perms = self.permissions(destination, author, is_owner)
        bot_perms = self.permissions(destination, me)
        content = message.content
        if not is_owner:
            if not author_perms.administrator:
                content = common_filters.filter_invites(content)
            if not author_perms.mention_everyone:
                content = common_filters.filter_mass_mentions(content)
        attachments = message.attachments
        files = []
        embed = None
        if attachments and author_perms.attach_files and bot_perms.attach_files:
            overs = await asyncio.gather(*(self.save_attach(file, files) for file in attachments))
            overs = list(filter(bool, overs))
            if overs:
                if bot_perms.embed_links:
                    embed = await self.get_embed(destination, overs)
                else:
                    content += (
                        "\n\n"
                        + _("Attachments:")
                        + "\n"
                        + "\n".join(f"({self.xbytes(a.size)}) {a.url}" for a in attachments)
                    )
        if not any((content, files, embed)):
            raise RiftError(_("No content to send."))
        if not is_owner or not send:
            content = f"{author}: {content}"
        return await send_coro(content=content, files=files, embed=embed)

    async def save_attach(self, file: discord.Attachment, files) -> discord.File:
        if file.size > max_size:
            return file
        buffer = BytesIO()
        await file.save(buffer, seek_begin=True)
        files.append(discord.File(buffer, file.filename))
        return None

    def xbytes(self, b):
        blist = ("B", "KB", "MB")
        index = 0
        while True:
            if b > 900:
                b = b / 1024.0
                index += 1
            else:
                return "{:.3g} {}".format(b, blist[index])

    # EVENTS

    @listener()
    async def on_message(self, m):
        if m.author.bot:
            return
        channel = m.author if isinstance(m.channel, discord.DMChannel) else m.channel
        sent = {}
        is_command = (await self.bot.get_context(m)).valid
        for rift, record in self.open_rifts.copy().items():
            if rift.source == channel and rift.author == m.author:
                if m.content.lower() == "exit":
                    processed = self.open_rifts.pop(rift)
                    if processed["notify"]:
                        with suppress(discord.HTTPException):
                            await rift.destination.send(
                                _("{} has closed the rift.").format(m.author)
                            )
                    await channel.send(_("Rift closed."))
                else:
                    if not is_command:
                        try:
                            record[m] = await self.process_message(rift, m, rift.destination)
                        except discord.HTTPException as e:
                            await channel.send(
                                _("I couldn't send your message due to an error: {}").format(e)
                            )
            elif rift.destination == channel:
                rift_chans = (rift.source, rift.destination)
                if rift_chans in sent:
                    record[m] = sent[rift_chans]
                else:
                    record[m] = sent[rift_chans] = await self.process_message(rift, m, rift.source)

    async def on_message_delete(self, m):
        if m.author.bot:
            return
        deleted = set()
        for record in self.open_rifts.copy().values():
            with suppress(KeyError, discord.NotFound):
                rifted = record.pop(m)
                if rifted not in deleted:
                    deleted.add(rifted)
                    await rifted.delete()

    async def on_message_edit(self, b, a):
        if a.author.bot:
            return
        channel = a.author if isinstance(a.channel, discord.DMChannel) else a.channel
        sent = set()
        for rift, record in self.open_rifts.copy().items():
            if rift.source == channel and rift.author == a.author:
                with suppress(KeyError, discord.NotFound):
                    await self.process_message(rift, a, record[a])
            elif rift.destination == channel:
                rift_chans = (rift.source, rift.destination)
                if rift_chans not in sent:
                    sent.add(rift_chans)
                    with suppress(KeyError, discord.NotFound):
                        await self.process_message(rift, a, record[a])
