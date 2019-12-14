import logging
from collections import defaultdict

import discord
from redbot.core.commands import Context
from redbot.core.utils import chat_formatting as CF


__all__ = ["ProxyEmbed"]
__author__ = "Zephyrkul"

LOG = logging.getLogger("red.fluffy.proxyembed")


# from: https://stackoverflow.com/a/34445090
def findall(p, s):
    """Yields all the positions of
    the pattern p in the string s."""
    i = s.find(p)
    while i != -1:
        yield i
        i = s.find(p, i + 1)


class _OverwritesEmbed(discord.Embed):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fields = defaultdict(
            lambda: defaultdict(lambda: self.Empty),
            {i: v for i, v in enumerate(getattr(self, "_fields", ()))},
        )

    @property
    def fields(self):
        return defaultdict(
            lambda: discord.embeds.EmbedProxy(),
            {i: discord.embeds.EmbedProxy(v) for i, v in self._fields.items()},
        )

    def add_field(self, *args, **kwargs):
        raise NotImplementedError("This operation is unsupported for overwrites.")


class ProxyEmbed(discord.Embed):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.overwrites = _OverwritesEmbed()

    @classmethod
    def _get(cls, obj, attr):
        try:
            obj = getattr(obj, attr)
        except AttributeError:
            try:
                # pylint: disable=E1136
                obj = obj[int(attr)]
            except (ValueError, IndexError, KeyError, TypeError):
                try:
                    # pylint: disable=E1136
                    obj = obj[attr]
                except (IndexError, KeyError, TypeError):
                    return cls.Empty
        return obj

    def _(self, *attrs):
        attrs = ".".join(map(str, attrs))
        overwrite = self.overwrites
        obj = self
        for attr in attrs.split("."):
            if overwrite is not self.Empty:
                overwrite = self._get(overwrite, attr)
            obj = self._get(obj, attr)
        if overwrite is not self.Empty:
            LOG.debug("Returning overwritten value %r", overwrite)
            return str(overwrite).strip()
        if obj is not self.Empty:
            return str(obj).strip()
        return self.Empty

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
        for i in range(len(self._fields)):
            inline, name, value = (
                self._("_fields", i, "inline"),
                self._("_fields", i, "name"),
                self._("_fields", i, "value"),
            )
            LOG.debug("index: %r, inline: %r, name: %r, value: %r", i, inline, name, value)
            if not inline or len(name) + len(value) > 78 or "\n" in name or "\n" in value:
                content.append(name)
                blocks = tuple(findall("```", value))
                if blocks == (0, len(value) - 3):
                    content.append(value)
                else:
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
