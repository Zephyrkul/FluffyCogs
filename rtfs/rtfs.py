import asyncio
import importlib
import inspect
import logging
import traceback
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import discord
import redbot
import yarl
from redbot.core import commands
from redbot.core.utils.chat_formatting import box, pagify

try:
    from discord.ext import menus
except ImportError:
    from redbot.vendored.discord.ext import menus


LOG = logging.getLogger("red.fluffy.rtfs")


class Unlicensed(Exception):
    pass


class SourceSource(menus.ListPageSource):
    def __init__(self, *args, header: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.header = header

    def format_page(self, menu, page):
        try:
            if page is None:
                if self.header.startswith("<"):
                    return self.header
                return {}
            return f"{self.header}\n{box(page, lang='py')}\nPage {menu.current_page + 1} / {self.get_max_pages()}"
        except Exception as e:
            # since d.py menus likes to suppress all errors
            LOG.debug(exc_info=e)
            raise


class SourceMenu(menus.MenuPages):
    async def finalize(self, timed_out):
        try:
            if self.message is None:
                return
            kwargs = await self._get_kwargs_from_page(None)
            if not kwargs:
                await self.message.delete()
            else:
                await self.message.edit(**kwargs)
        except Exception as e:
            # since d.py menus likes to suppress all errors
            LOG.debug(exc_info=e)
            raise


class Env(dict):
    def __missing__(self, key):
        try:
            module = importlib.import_module(key)
        except ImportError:
            raise KeyError(key) from None
        else:
            self[key] = module
            return module


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
        header: str = ""
        if full_module:
            if full_module.startswith("discord."):
                is_installed = True
                if discord.__version__[-1].isdigit():
                    dpy_commit = "v" + discord.__version__
                else:
                    try:
                        dpy_version = version("discord.py").split("+g")
                    except PackageNotFoundError:
                        dpy_commit = "master"
                    else:
                        dpy_commit = dpy_version[1] if len(dpy_version) == 2 else "master"
                header = f"<https://github.com/Rapptz/discord.py/blob/{dpy_commit}/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}>"
            elif full_module.startswith("redbot."):
                is_installed = True
                if "dev" in redbot.__version__:
                    red_commit = "V3/develop"
                else:
                    red_commit = redbot.__version__
                header = f"<https://github.com/Cog-Creators/Red-DiscordBot/blob/{red_commit}/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}>"
            elif dl := ctx.bot.get_cog("Downloader"):
                is_installed, installable = await dl.is_installed(full_module.split(".")[0])
                if is_installed:
                    if installable.repo is None:
                        is_installed = False
                    else:
                        if (
                            "mikeshardmind" in installable.repo.url.lower()
                            or "sinbad" in installable.repo.url.lower()
                        ):
                            # Sinbad's license specifically disallows redistribution of code, as per Section 3.
                            #   Ref: https://github.com/mikeshardmind/SinbadCogs/blob/9cdcd042d57cc39c7330fcda50ecf580c055c313/LICENSE#L73-L76
                            # Raising OSError here will prevent even bot owners from viewing the code.
                            raise Unlicensed()
                        else:
                            url = yarl.URL(installable.repo.url)
                            if url.user or url.password:
                                is_installed = False
                            header = f"<{installable.repo.clean_url.rstrip('/')}/blob/{installable.commit}/{full_module.replace('.', '/')}.py#L{line}-L{line + len(lines) - 1}>"
        if not is_installed and not is_owner:
            # don't disclose the source of private cogs
            raise OSError()
        if not header:
            if module:
                header = box(f"File {source_file}, line {line}, in module {module}", lang="py")
            else:
                header = box(f"File {source_file}, line {line}", lang="py")
        raw_pages = list(
            pagify(
                "".join(lines).replace("```", "`\u200b`\u200b`"), shorten_by=10, page_length=1024
            )
        )
        await SourceMenu(
            SourceSource(raw_pages, per_page=1, header=header), clear_reactions_after=True
        ).start(ctx)

    @commands.command(aliases=["rts", "source"])
    async def rtfs(self, ctx: commands.Context, *, thing: str):
        """
        Read the source code for a cog or command.

        The bot owner may additionally supply any valid Python object,
        if developer mode is enabled.
        """
        is_owner = await ctx.bot.is_owner(ctx.author)
        try:
            if obj := ctx.bot.get_cog(thing):
                return await self.format_and_send(ctx, obj, is_owner=is_owner)
            elif obj := ctx.bot.get_command(thing):
                return await self.format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find source file for `{thing}`")
        except Unlicensed:
            return await ctx.send(
                f"The source code for `{thing}` is copyrighted under too strict a license for me to show it here."
            )
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
            dpy_commands=discord.ext.commands,
        )
        try:
            obj = eval(thing, env)
        except BaseException as be:
            return await ctx.send(
                box("".join(traceback.format_exception_only(type(be), be)), lang="py")
            )
        try:
            return await self.format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find source file for object `{thing}`")
        except TypeError as te:
            return await ctx.send(
                box("".join(traceback.format_exception_only(type(te), te)), lang="py")
            )
