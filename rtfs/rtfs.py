import asyncio
import importlib
import inspect
import traceback
from typing import Any

import discord
import redbot
import yarl
from redbot.core import commands
from redbot.core.utils.chat_formatting import box, pagify


class Env(dict):
    def __missing__(self, key):
        try:
            return importlib.import_module(key)
        except ImportError:
            raise KeyError(key) from None


class RTFS(commands.Cog):
    async def red_get_data_for_user(self, *, user_id):
        return {}  # Nothing to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # Nothing to delete

    @staticmethod
    async def format_and_send(ctx: commands.Context, obj: Any, *, is_owner: bool = False) -> None:
        source = obj
        if isinstance(obj, commands.Cog):
            source = type(obj)
        elif isinstance(obj, commands.Command):
            source = obj.callback
            if not source.__module__:
                # probably some kind of custom-coded command
                if is_owner:
                    return await ctx.invoke(
                        ctx.bot.get_command("instantcmd source"), command=obj.qualified_name
                    )
                else:
                    raise OSError
        elif isinstance(obj, property):
            source = obj.fget
        elif isinstance(obj, (discord.utils.cached_property, discord.utils.CachedSlotProperty)):
            source = obj.function
        try:
            lines, line = inspect.getsourcelines(source)
            source_file = inspect.getsourcefile(source)
        except TypeError:
            if isinstance(source, type):
                raise
            source = type(source)
            lines, line = inspect.getsourcelines(source)
            source_file = inspect.getsourcefile(source)
        module = getattr(inspect.getmodule(source), "__name__", None)
        if source_file and module and source_file.endswith("__init__.py"):
            full_module = f"{module}.__init__"
        else:
            full_module = module
        is_installed = False
        if full_module:
            if full_module.startswith("discord."):
                is_installed = True
                if discord.__version__[-1].isdigit():
                    await ctx.send(
                        f"https://github.com/Rapptz/discord.py/blob/v{discord.__version__}/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}"
                    )
                else:
                    assert discord.__version__.startswith("1.")
                    await ctx.send(
                        f"https://github.com/Rapptz/discord.py/tree/master/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}"
                    )
            elif full_module.startswith("redbot."):
                is_installed = True
                if "dev" in redbot.__version__:
                    assert redbot.__version__.startswith("3.")
                    await ctx.send(
                        f"https://github.com/Cog-Creators/Red-DiscordBot/tree/V3/develop/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}"
                    )
                else:
                    await ctx.send(
                        f"https://github.com/Cog-Creators/Red-DiscordBot/blob/{redbot.__version__}/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}"
                    )
            elif dl := ctx.bot.get_cog("Downloader"):
                is_installed, installable = await dl.is_installed(full_module.split(".")[0])
                if is_installed:
                    if installable.repo is None:
                        is_installed = False
                    else:
                        url = yarl.URL(installable.repo.url)
                        if url.user or url.password:
                            is_installed = False
                        else:
                            await ctx.send(
                                f"{installable.repo.url.rstrip('/')}/blob/{installable.repo.commit}/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}"
                            )
        if not is_installed and is_owner:
            if module:
                await ctx.send(
                    box(f"File {source_file!r}, line {line}, in module {module}", lang="py")
                )
            else:
                await ctx.send(box(f"File {source_file!r}, line {line}", lang="py"))
        elif not is_installed:
            # don't disclose the source of private cogs
            raise OSError()
        await ctx.send_interactive(
            pagify("".join(lines), shorten_by=10), box_lang="py", timeout=300
        )

    @commands.command(aliases=["rts", "source"])
    async def rtfs(self, ctx: commands.Context, *, thing: str):
        """
        Read the source code for a cog or command.

        The bot owner may additionally supply any valid Python object.
        """
        is_owner = await ctx.bot.is_owner(ctx.author)
        try:
            if obj := ctx.bot.get_cog(thing):
                return await self.format_and_send(ctx, obj, is_owner=is_owner)
            elif obj := ctx.bot.get_command(thing):
                return await self.format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find source file for `{thing}`")
        dev = ctx.bot.get_cog("Dev")
        if not is_owner or not dev:
            raise commands.UserFeedbackCheckFailure(
                f"I couldn't find any cog or command named `{thing}`."
            )
        thing = dev.cleanup_code(thing)
        env = Env(
            bot=ctx.bot,
            ctx=ctx,
            channel=ctx.channel,
            author=ctx.author,
            guild=ctx.guild,
            message=ctx.message,
            asyncio=asyncio,
            commands=commands,
            __name__="__main__",
        )
        try:
            obj = eval(thing, env)
        except:
            return await ctx.send_interactive(
                pagify(traceback.format_exc(), shorten_by=10), box_lang="py"
            )
        try:
            return await self.format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find source file for object `{thing}`")
        except TypeError as te:
            return await ctx.send(
                box("".join(traceback.format_exception_only(type(te), te)), lang="py")
            )
