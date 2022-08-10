import inspect
import logging
import re
import traceback
from functools import partial, partialmethod
from importlib.metadata import PackageNotFoundError, version
from itertools import chain
from typing import TYPE_CHECKING, Any, Optional

import discord
import redbot
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify

try:
    from discord.ext import menus
except ImportError:
    from redbot.vendored.discord.ext import menus

if TYPE_CHECKING:
    from redbot.cogs.downloader import Downloader


LOG = logging.getLogger("red.fluffy.rtfs")
GIT_AT = re.compile(r"(?i)git@(?P<host>[^:]+):(?P<user>[^/]+)/(?P<repo>.+)(?:\.git)?")


class Unlicensed(Exception):
    """
    Exception class for when the source code is known to have too restrictive of a license to redistribute code.
    """

    def __init__(self, *args, cite: str = None, **kwargs):
        super.__init__(*args, **kwargs)
        self.cite = cite


class NoLicense(Exception):
    """
    Exception class for when the source code is known to have no license.
    """


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
            LOG.debug("Exception in SourceSource", exc_info=e)
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
            LOG.debug("Exception in SourceMenu", exc_info=e)
            raise


class RTFS(commands.Cog):
    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        bot.add_dev_env_value(
            "rtfs", lambda ctx: partial(self.format_and_send, ctx, is_owner=True)
        )

    def cog_unload(self):
        self.bot.remove_dev_env_value("rtfs")

    async def red_get_data_for_user(self, *, user_id):
        return {}  # Nothing to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # Nothing to delete

    @classmethod
    async def format_and_send(
        cls, ctx: commands.Context, obj: Any, *, is_owner: bool = False
    ) -> None:
        obj = inspect.unwrap(obj)
        source = obj
        if isinstance(obj, commands.Command):
            source = obj.callback
            if not inspect.getmodule(source):
                # probably some kind of custom-coded command
                if is_owner and (cmd := ctx.bot.get_command("instantcmd source")):
                    return await cmd(ctx, command=obj.qualified_name)
                else:
                    raise OSError
        elif isinstance(obj, (partial, partialmethod)):
            source = obj.func
        elif isinstance(obj, property):
            source = obj.fget
        elif isinstance(obj, (discord.utils.cached_property, discord.utils.CachedSlotProperty)):
            source = obj.function
        try:
            lines, line = inspect.getsourcelines(source)
        except TypeError as e:
            if "was expected, got" not in e.args[0]:
                raise
            source = type(source)
            lines, line = inspect.getsourcelines(source)
        source_file = inspect.getsourcefile(source)
        comments = inspect.getcomments(source) if line > 0 else ""
        module = getattr(inspect.getmodule(source), "__name__", None)
        if source_file and module and source_file.endswith("__init__.py"):
            full_module = f"{module}.__init__"
        else:
            full_module = module
        is_installed = False
        # no reason to highlight entire files
        line_suffix = f"#L{line}-L{line + len(lines) - 1}" if line > 0 else ""
        header: str = ""
        if full_module:
            dl: Optional[Downloader]
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
                header = f"<https://github.com/Rapptz/discord.py/blob/{dpy_commit}/{full_module.replace('.', '/')}.py{line_suffix}>"
            elif full_module.startswith("redbot."):
                is_installed = True
                if "dev" in redbot.__version__:
                    red_commit = "V3/develop"
                else:
                    red_commit = redbot.__version__
                header = f"<https://github.com/Cog-Creators/Red-DiscordBot/blob/{red_commit}/{full_module.replace('.', '/')}.py{line_suffix}>"
            elif dl := ctx.bot.get_cog("Downloader"):
                is_installed, installable = await dl.is_installed(full_module.split(".")[0])
                if is_installed:
                    if installable.repo is None:
                        is_installed = False
                    else:
                        if ctx.guild or not is_owner:
                            surl = str(installable.repo.url).lower()
                            if (
                                "mikeshardmind/sinbadcogs" in surl
                                and installable.repo.branch.lower() == "v3"
                            ):
                                # Sinbad's license specifically disallows redistribution of code, as per Section 3.
                                raise Unlicensed(
                                    cite="<https://github.com/mikeshardmind/SinbadCogs/blob/v3/LICENSE#L73-L76>"
                                )
                            elif "aikaterna/gobcog" in surl:
                                raise NoLicense()
                            elif "aikaterna/imgwelcome" in surl:
                                raise NoLicense()
                        if match := GIT_AT.match(installable.repo.url):
                            # SSH URL
                            # Since it's not possible to tell if it's a private repo or not without an extra web request,
                            # we'll just assume it's a private repo
                            is_installed = False
                            repo_url = f"https://{match.group('host')}/{match.group('user')}/{match.group('repo')}"
                        else:
                            repo_url = installable.repo.clean_url
                            if repo_url != installable.repo.url:
                                # Private repo
                                is_installed = False
                            repo_url = repo_url.rstrip("/")
                        header = f"<{repo_url}/blob/{installable.commit}/{full_module.replace('.', '/')}.py{line_suffix}>"
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
                "".join(chain([comments], lines) if comments else lines).replace(
                    # \u02CB = modifier letter grave accent
                    "```",
                    "\u02CB\u02CB\u02CB",
                ),
                shorten_by=10,
                page_length=1024,
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
                return await self.format_and_send(ctx, type(obj), is_owner=is_owner)
            elif obj := ctx.bot.get_command(thing):
                return await self.format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find source file for `{thing}`")
        except Unlicensed as e:
            if e.cite:
                message = f"The source code for `{thing}` is copyrighted under too strict a license for me to show it here. (See {e.cite})"
            else:
                message = f"The source code for `{thing}` is copyrighted under too strict a license for me to show it here."
            return await ctx.send(message)
        except NoLicense:
            return await ctx.send(
                f"The source code for `{thing}` has no license, so I cannot show it here."
            )
        dev = ctx.bot.get_cog("Dev")
        if not is_owner or not dev:
            raise commands.UserFeedbackCheckFailure(
                f"I couldn't find any cog or command named `{thing}`."
            )
        thing = dev.cleanup_code(thing)
        env = dev.get_environment(ctx)
        try:
            obj = eval(thing, env)
        except NameError:
            return await ctx.send(f"I couldn't find any cog, command, or object named `{thing}`.")
        except Exception as e:
            return await ctx.send(
                box("".join(traceback.format_exception_only(type(e), e)), lang="py")
            )
        try:
            return await self.format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find source file for object `{thing}`")
        except TypeError as te:
            return await ctx.send(
                box("".join(traceback.format_exception_only(type(te), te)), lang="py")
            )
