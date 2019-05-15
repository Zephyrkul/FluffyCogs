import collections
import io
import itertools
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, date, time
from typing import Optional

import discord

from redbot.core import commands
from redbot.core.i18n import Translator, cog_i18n


Cog = getattr(commands, "Cog", object)


_T = Translator("LogsFrom", __file__)


@dataclass
class MHeaders:
    author: discord.User
    created: datetime
    edited: Optional[datetime] = None

    def to_str(self, other: "MHeaders") -> str:
        final = []
        if self.author != other.author:
            if other.author:
                final.append("")
            auth = self.author.display_name
            if self.author.bot:
                auth += " [BOT]"
            final.append(auth)
        if self.edited:
            if self.edited.date() == self.created.date():
                ed = ", edited {:%X}".format(self.edited.time())
            else:
                ed = ", edited {:%c}".format(self.edited)
        else:
            ed = ""
        if other.created and self.created.date() == other.created.date():
            final.append("[{:%X}{}] ".format(self.created.time(), ed))
        else:
            final.append("[{:%c}{}] ".format(self.created, ed))
        return "\n".join(final)


def positive_int(argument):
    try:
        i = int(argument)
    except ValueError as e:
        raise commands.BadArgument(_T("Please use a positive number.")) from e
    if i <= 0:
        raise commands.BadArgument(_T("Please use a positive number."))
    return i


@cog_i18n(_T)
class LogsFrom(Cog):
    @commands.group(invoke_without_command=True)
    async def logsfrom(
        self, ctx, limit: positive_int = 100, *, channel: discord.TextChannel = None
    ):
        """Logs the channel into a file, then uploads the file.

        The limit may be the number of messages to log or the ID of the message to start after, exclusive.
        All timestamps are in UTC."""
        channel = channel or ctx.channel
        if not channel.permissions_for(ctx.author).read_message_history:
            raise commands.MissingPermissions(["read_message_history"])
        if not channel.permissions_for(ctx.me).read_message_history:
            raise commands.BotMissingPermissions(["read_message_history"])
        async with ctx.typing():
            kwargs = {"after": discord.Object(id=limit), "limit": limit}
            if channel == ctx.channel:
                kwargs["before"] = ctx.message
            stream = io.BytesIO()
            last_h = MHeaders(None, None)
            try:
                messages = await channel.history(**kwargs, oldest_first=False).flatten()
            except TypeError:
                messages = await channel.history(**kwargs, reverse=False).flatten()
            processed = len(messages)
            for _ in range(processed):
                m = messages.pop()
                now_h = MHeaders(m.author, m.created_at, m.edited_at)
                headers = now_h.to_str(last_h)
                last_h = now_h
                if headers:
                    stream.write(headers.encode("utf-8"))
                stream.write(m.clean_content.encode("utf-8"))
                if m.attachments:
                    stream.write(b"\n")
                    stream.write(
                        "; ".join(f"[{a.filename}]({a.url})" for a in m.attachments).encode(
                            "utf-8"
                        )
                    )
                stream.write(b"\n")
            stream.seek(0)
            return await ctx.send(
                content=_T("{} messages logged.").format(processed),
                file=discord.File(stream, filename=f"{channel.name}.md"),
                delete_after=300,
            )
