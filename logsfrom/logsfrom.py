import collections
import io
import itertools
import sys

import discord

from redbot.core import commands
from redbot.core.i18n import Translator, cog_i18n


Cog = getattr(commands, "Cog", object)


_ = Translator("LogsFrom", __file__)


MHeaders = collections.namedtuple("MHeaders", ("author", "created"), defaults=("", ""))


def positive_int(argument):
    i = int(argument)
    if i <= 0:
        raise ValueError
    return i


@cog_i18n(_)
class LogsFrom(Cog):
    def __init__(self):
        self.active = set()

    @commands.group(invoke_without_command=True)
    async def logsfrom(
        self, ctx, limit: positive_int = 100, *, channel: discord.TextChannel = None
    ):
        """Logs the channel into a file, then uploads the file.

        The limit may be the number of messages to log or the ID of the message to start after, exclusive.
        All timestamps are in UTC."""
        channel = channel or ctx.channel
        if not channel.permissions_for(ctx.author).read_message_history:
            return
        if not channel.permissions_for(ctx.me).read_message_history:
            return await ctx.send(
                _("I don't have permission to read the history of {}.").format(channel)
            )
        if channel in self.active:
            return await ctx.send(
                _(
                    "I am already logging messages in this channel. "
                    "Use `[p]logsfrom cancel` to cancel."
                )
            )
        self.active.add(channel)
        self.active.add((ctx.author, channel))
        async with ctx.typing():
            kwargs = {"after": discord.Object(id=limit), "limit": limit, "reverse": True}
            if channel == ctx.channel:
                kwargs["before"] = ctx.message
            stream = io.BytesIO()
            last_h = MHeaders()
            processed = 0
            async for m in channel.history(**kwargs):
                if channel not in self.active:
                    break
                author_h = m.author.display_name + (" [BOT]" if m.author.bot else "")
                author_h = "" if last_h.author == author_h else author_h
                created_h = m.created_at.strftime("%X %x")
                edited_h = m.edited_at.strftime("%X %x") if m.edited_at else ""
                i = 0
                for i in range(min(len(created_h), len(edited_h))):
                    if created_h[i] != edited_h[i]:
                        break
                edited_h = f"(edited: {edited_h[i:]})" if edited_h[i:] else ""
                for i in range(min(len(last_h.created), len(created_h))):
                    if last_h.created[i] != created_h[i]:
                        break
                created_h = created_h[i:]
                last_h = MHeaders(author_h, created_h)
                headers = " ".join(filter(bool, (author_h, created_h, edited_h)))
                if headers:
                    stream.write(headers.encode("utf-8"))
                    stream.write(b"\n")
                stream.write(m.clean_content.encode("utf-8"))
                if m.attachments:
                    stream.write(b"\n")
                    stream.write(
                        "; ".join(f"[{a.filename}]({a.url})" for a in m.attachments).encode(
                            "utf-8"
                        )
                    )
                stream.write(b"\n\n")
                processed += 1
            self.active.discard(channel)
            self.active.discard((ctx.author, channel))
            stream.seek(0)
            return await ctx.send(
                content=_("{} messages logged.").format(processed),
                file=discord.File(stream, filename=f"{channel.name}.md"),
                delete_after=300,
            )

    @logsfrom.command()
    async def cancel(self, ctx, *, channel: discord.TextChannel = None):
        """Cancels logging in the specified channel.

        Any progress made is returned."""
        channel = channel or ctx.channel
        if channel not in self.active:
            return await ctx.send(_("I am not currently logging that channel."))
        if (ctx.author, channel) not in self.active:
            return await ctx.send(_("You can't cancel another member's logging."))
        self.active.discard(channel)
        self.active.discard((ctx.author, channel))
        await ctx.send("Logging cancelled. Sending unfinished log...")
