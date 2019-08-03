import discord
from redbot.core.commands import Context
from redbot.core.utils import chat_formatting as CF


__all__ = ["ProxyEmbed"]
__author__ = "Zephyrkul"


class ProxyEmbed(discord.Embed):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.overwrites = {}

    def _(self, *attrs):
        attrs = ".".join(map(str, attrs))
        overwrite = self.overwrites
        obj = self
        for attr in attrs.split("."):
            if overwrite is not None:
                overwrite = overwrite.get(attr)
            try:
                obj = getattr(obj, attr)
            except AttributeError:
                try:
                    # pylint: disable=E1136
                    obj = obj[int(attr)]
                except ValueError:
                    # pylint: disable=E1136
                    obj = obj[attr]
        if overwrite:
            return overwrite
        return obj

    async def send_to(self, ctx: Context, content=None):
        if await ctx.embed_requested():
            return await ctx.send(content=content, embed=self)
        content = str(content) if content is not None else None
        if content:
            content = [content, ""]
        else:
            content = []
        next_break = False
        title = self._("title")
        if title:
            content.append(CF.bold(title))
            next_break = True
        name = self._("author.name")
        if name:
            content.append(CF.italics(name))
            next_break = True
        url = self._("thumbnail.url")
        if url and not url.startswith("attachment://"):
            content.append(f"<{url}>")
            next_break = True
        description = self._("description")
        if description:
            content.append(CF.box(CF.escape(description, formatting=True)))
            next_break = False
        if next_break:
            content.append("")
            next_break = False
        for i in range(len(self.fields)):
            inline, name, value = (
                self._("fields", i, "inline"),
                self._("fields", i, "name"),
                self._("fields", i, "value"),
            )
            if not inline or len(name) + len(value) > 78 or "\n" in name or "\n" in value:
                content.append(name)
                content.append(CF.box(CF.escape(value, formatting=True)))
                next_break = False
            else:
                content.append(f"{name}: {value}")
                next_break = True
        if next_break:
            content.append("")
            next_break = False
        url = self._("image.url")
        if url and not url.startswith("attachment://"):
            content.append(f"<{url}>")
        url = self._("video.url")
        if url and not url.startswith("attachment://"):
            content.append(f"<{url}>")
        text, timestamp = self._("footer.text"), self._("timestamp")
        if text and timestamp:
            content.append(f"{text} | {timestamp}")
        elif text:
            content.append(text)
        elif timestamp:
            content.append(f"{timestamp} UTC")
        content = list(CF.pagify("\n".join(map(str, content)), shorten_by=0))
        return await ctx.send_interactive(content)
