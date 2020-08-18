import asyncio
import collections
import io
import itertools
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union

import discord
from redbot.core import commands
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.mod import check_permissions
from redbot.core.utils.predicates import MessagePredicate

_T = Translator("LogsFrom", __file__)


@dataclass
class MHeaders:
    author: discord.User
    created: datetime
    edited: Optional[datetime]

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


async def history(channel, **kwargs):
    d = collections.deque()
    async for message in channel.history(**kwargs):
        d.append(message)
    return d


MaybeMessage = Optional[Union[int, discord.Message]]  # yes, this order is intentional


@cog_i18n(_T)
class LogsFrom(commands.Cog):
    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    @commands.command(usage="[bounds...] [channel]")
    async def logsfrom(
        self,
        ctx,
        after: MaybeMessage = None,
        before: MaybeMessage = None,
        *,
        channel: discord.TextChannel = None,
    ):
        """
        Logs the specified channel into a file, then uploads the file.

        The channel will default to the current channel if none is specified.
        The limit may be the number of messages to log or the ID of the message to start after, exclusive.
        All timestamps are in UTC.
        """
        if channel:
            ctxc = copy(ctx)
            ctxc.channel = channel
        else:
            channel = ctx.channel
            ctxc = ctx
        if not channel.permissions_for(ctx.me).read_message_history:
            raise commands.BotMissingPermissions(["read_message_history"])
        if not await check_permissions(ctxc, {"read_message_history": True}):
            raise commands.MissingPermissions(["read_message_history"])
        after, before = getattr(after, "id", after), getattr(before, "id", before)
        cancel_task = asyncio.ensure_future(
            ctx.bot.wait_for("message", check=MessagePredicate.cancelled(ctx))
        )
        async with ctx.typing():
            kwargs = {"oldest_first": False}
            if not after and not before:
                kwargs["limit"] = 100
            elif not before:
                kwargs.update(after=discord.Object(id=after), limit=after)
            elif not after:
                raise RuntimeError("This should never happen.")
            else:
                before = min((ctx.message.id, before))
                # TODO: wtf should this shit even *mean*
                if after >= before:
                    kwargs.update(after=discord.Object(id=after), limit=before, oldest_first=True)
                else:
                    kwargs.update(
                        after=discord.Object(id=after),
                        before=discord.Object(id=before),
                        limit=min((before, after)),
                    )
            print(kwargs)
            stream = io.BytesIO()
            last_h = MHeaders(None, None, None)
            message_task = asyncio.ensure_future(history(channel, **kwargs))
            done, _ = await asyncio.wait(
                (cancel_task, message_task), return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done:
                message_task.cancel()
                return await ctx.send(_T("Okay, I've cancelled my logging."))
            messages = message_task.result()
            processed = 0
            if kwargs["oldest_first"]:
                pop = messages.popleft
            else:
                pop = messages.pop
            while messages:
                await asyncio.sleep(0)
                if cancel_task.done():
                    return await ctx.send(_T("Okay, I've cancelled my logging."))
                message = pop()
                now_h = MHeaders(message.author, message.created_at, message.edited_at)
                headers = now_h.to_str(last_h)
                last_h = now_h
                if headers:
                    stream.write(headers.encode("utf-8"))
                stream.write(message.clean_content.encode("utf-8"))
                if message.attachments:
                    stream.write(b"\n")
                    stream.write(
                        "; ".join(f"[{a.filename}]({a.url})" for a in message.attachments).encode(
                            "utf-8"
                        )
                    )
                stream.write(b"\n")
                processed += 1
            cancel_task.cancel()
            stream.seek(0)
            return await ctx.send(
                content=_T("{} message{s} logged.").format(
                    processed, s=("" if processed == 1 else "s")
                ),
                file=discord.File(stream, filename=f"{channel.name}.md"),
                delete_after=300,
            )
