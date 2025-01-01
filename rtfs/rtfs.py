import ast
import inspect
import logging
import sys
import textwrap
import traceback
from functools import partial, partialmethod
from importlib.metadata import PackageNotFoundError, version
from itertools import chain, product
from math import ceil
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Optional, Union

import discord
import redbot
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.dev_commands import cleanup_code
from redbot.core.utils.chat_formatting import box, pagify

from .pages import Pages

if sys.version_info < (3, 10):
    from typing import Iterable, Iterator, TypeVar

    _T = TypeVar("_T")

    def pairwise(iterable: Iterable[_T]) -> "Iterator[tuple[_T, _T]]":
        iterator = iter(iterable)
        a = next(iterator, ...)
        for b in iterator:
            yield a, b
            a = b
else:
    from itertools import pairwise

try:
    import regex as re
except ImportError:
    import re

if TYPE_CHECKING:
    from redbot.cogs.downloader import Downloader
    from redbot.core.dev_commands import Dev

LOG = logging.getLogger("red.fluffy.rtfs")
GIT_AT = re.compile(r"(?i)git@(?P<host>[^:]+):(?P<user>[^/]+)/(?P<repo>.+)(?:\.git)?")


class Unlicensed(Exception):
    """
    Exception class for when the source code is known to have too restrictive of a license to redistribute code.
    """

    def __init__(self, *, cite: Optional[str] = None):
        super().__init__(cite)
        self.cite = cite


class NoLicense(Exception):
    """
    Exception class for when the source code is known to have no license.
    """


def _wrap_with_linereturn(wrapper: textwrap.TextWrapper):
    def inner(line: str):
        wrapped = wrapper.wrap(line)
        if not wrapped:
            return ["\n"]
        wrapped[-1] += "\n"
        return wrapped

    return inner


def _pager(source: str, *, header: Optional[str]):
    # \u02CB = modifier letter grave accent
    source = source.replace("```", "\u02cb\u02cb\u02cb")
    header = header or ""
    max_page = 1990 - len(header)
    if len(source) + 100 < max_page:
        # fast path
        yield f"{header}```py\n{source}\n```"
        return
    wrapper = textwrap.TextWrapper(88, tabsize=4)
    lines: list[str] = list(
        # split overly long lines to allow for page breaks
        chain.from_iterable(map(_wrap_with_linereturn(wrapper), source.splitlines()))
    )
    total_lines = len(lines)
    num_pages = ceil((len(source) + 600) / max_page)
    format = f"{header}```py\n%s\n```".__mod__
    pages: map[str] = map(
        lambda t: "".join(lines[slice(*t)]).rstrip(),
        pairwise(
            chain(
                map(
                    lambda x: round(x / num_pages), range(0, total_lines * num_pages, total_lines)
                ),
                (None,),
            )
        ),
    )
    for page in pages:
        if len(page) > max_page:
            # degenerate case
            yield from map(format, pagify(page, page_length=max_page, shorten_by=0))
        elif page:
            yield format(page)


async def format_and_send(ctx: commands.Context, obj: Any, *, is_owner: bool = False) -> None:
    obj = inspect.unwrap(obj)
    source: Any
    if isinstance(
        obj, (commands.Command, discord.app_commands.Command, discord.app_commands.ContextMenu)
    ):
        source = obj.callback
        if not inspect.getmodule(source):
            # probably some kind of custom-coded command
            cog: Any = ctx.bot.get_cog("InstantCommands")
            if not is_owner or cog is None:
                raise OSError
            for snippet in cog.code_snippets:
                if snippet.verbose_name == obj.name:
                    header = f"__command `{snippet.verbose_name}`__"
                    await Pages(
                        source=_pager(snippet.source, header=header),
                        author_id=ctx.author.id,
                        timeout_content=None,
                        timeout=60,
                    ).send_to(ctx)
                    return
            raise OSError
    elif isinstance(obj, (partial, partialmethod)):
        source = obj.func
    elif isinstance(obj, property):
        source = obj.fget
    elif isinstance(obj, (discord.utils.cached_property, discord.utils.CachedSlotProperty)):
        source = obj.function  # type: ignore
    else:
        source = getattr(obj, "__func__", obj)
    try:
        lines, line = inspect.getsourcelines(source)
    except TypeError as e:
        if "was expected, got" not in e.args[0]:
            raise
        source = type(source)
        lines, line = inspect.getsourcelines(source)
    source_file = inspect.getsourcefile(source)
    if line > 0:
        comments = inspect.getcomments(source) or ""
        line_suffix, _ = next(
            filter((lambda item: not re.match(r"\s*@", item[1])), enumerate(lines, line))
        )
        line_suffix = f"#L{line_suffix}"
    else:
        comments = ""
        line_suffix = ""
    module = getattr(inspect.getmodule(source), "__name__", None)
    if source_file and module and source_file.endswith("__init__.py"):
        full_module = f"{module}.__init__"
    else:
        full_module = module
    is_installed = False
    header: str = ""
    if full_module:
        dl: Optional[Downloader] = ctx.bot.get_cog("Downloader")
        full_module_path = full_module.replace(".", "/")
        if full_module.startswith("discord."):
            is_installed = True
            if discord.__version__[-1].isdigit():
                dpy_commit = "v" + discord.__version__
            else:
                try:
                    _, _, dpy_commit = version("discord.py").partition("+g")
                except PackageNotFoundError:
                    dpy_commit = "master"
            dpy_commit = dpy_commit or "master"
            header = f"https://github.com/Rapptz/discord.py/blob/{dpy_commit}/{full_module_path}.py{line_suffix}"
        elif full_module.startswith("redbot."):
            is_installed = not redbot.version_info.dirty
            if redbot.version_info.dev_release:
                red_commit = redbot.version_info.short_commit_hash or "V3/develop"
            else:
                red_commit = redbot.__version__
            if is_installed:
                header = f"https://github.com/Cog-Creators/Red-DiscordBot/blob/{red_commit}/{full_module_path}.py{line_suffix}"
        elif dl:
            is_installed, installable = await dl.is_installed(full_module.split(".")[0])
            if is_installed:
                assert installable
                if installable.repo is None:
                    is_installed = False
                else:
                    if ctx.guild or not is_owner:
                        surl = str(installable.repo.url).lower()
                        if "aikaterna/gobcog" in surl or "aikaterna/imgwelcome" in surl:
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
                    header = f"{repo_url}/blob/{installable.commit}/{full_module.replace('.', '/')}.py{line_suffix}"
    if not is_installed and not is_owner:
        # don't disclose the source of private cogs
        raise OSError()
    if not header:
        if module:
            header = f"```py\nFile {source_file}, line {line}, in module {module}\n```"
        else:
            header = f"```py\nFile {source_file}, line {line}\n```"
    else:
        header = f"<{header}>"
    comments = comments and dedent(comments)
    lines = dedent("".join(lines))
    await Pages(
        source=_pager(f"{comments}{lines}", header=header),
        author_id=ctx.author.id,
        timeout_content=header if header.startswith("<") else None,
        timeout=60,
    ).send_to(ctx)


def _find_app_command(
    tree: discord.app_commands.CommandTree, guild: Optional[discord.Guild], name: str
) -> Union[
    discord.app_commands.Command,
    discord.app_commands.ContextMenu,
    discord.app_commands.Group,
    None,
]:
    return next(
        filter(
            None,
            (
                tree.get_command(name, guild=scope, type=command_type)
                for scope, command_type in product(
                    (guild, None) if guild else (None,), discord.AppCommandType
                )
            ),
        ),
        None,
    )


class RTFS(commands.Cog):
    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dev_env_value(
            "rtfs", lambda ctx: partial(format_and_send, ctx=ctx, is_owner=True)
        )

    async def cog_unload(self) -> None:
        self.bot.remove_dev_env_value("rtfs")

    @commands.command(aliases=["rts", "source"])
    async def rtfs(self, ctx: commands.Context, *, thing: str):
        """
        Read the source code for a cog or command.

        The bot owner may additionally supply any valid Python object,
        if developer mode is enabled.
        """
        is_owner = await ctx.bot.is_owner(ctx.author)
        try:
            if thing.startswith("/") and (
                obj := _find_app_command(ctx.bot.tree, ctx.guild, thing[1:])
            ):
                return await format_and_send(ctx, obj, is_owner=is_owner)
            elif obj := ctx.bot.get_cog(thing):
                return await format_and_send(ctx, type(obj), is_owner=is_owner)
            elif obj := ctx.bot.get_command(thing):
                return await format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find any source file for `{thing}`")
        except Unlicensed as e:
            if e.cite:
                message = f"The source code for `{thing}` is copyrighted under too strict a license for me to show it here. (See <{e.cite}>)"
            else:
                message = f"The source code for `{thing}` is copyrighted under too strict a license for me to show it here."
            return await ctx.send(message)
        except NoLicense:
            return await ctx.send(
                f"The source code for `{thing}` has no license, so I cannot show it here."
            )
        dev: Optional[Dev] = ctx.bot.get_cog("Dev")
        if not is_owner or not dev:
            raise commands.UserFeedbackCheckFailure(
                f"I couldn't find any cog or command named `{thing}`."
            )
        thing = cleanup_code(thing)
        env = dev.get_environment(ctx)
        env["getattr_static"] = inspect.getattr_static
        try:
            tree = ast.parse(thing, "<rtfs>", "eval")
            if isinstance(tree.body, ast.Attribute) and isinstance(tree.body.ctx, ast.Load):
                tree.body = ast.Call(
                    func=ast.Name(id="getattr_static", ctx=ast.Load()),
                    args=[tree.body.value, ast.Constant(value=tree.body.attr)],
                    keywords=[],
                )
                tree = ast.fix_missing_locations(tree)
            obj = eval(compile(tree, "<rtfs>", "eval"), env)
        except NameError:
            return await ctx.send(f"I couldn't find any cog, command, or object named `{thing}`.")
        except Exception as e:
            return await ctx.send(
                box("".join(traceback.format_exception_only(type(e), e)), lang="py")
            )
        try:
            return await format_and_send(ctx, obj, is_owner=is_owner)
        except OSError:
            return await ctx.send(f"I couldn't find source file for object `{thing}`")
        except TypeError as te:
            return await ctx.send(
                box("".join(traceback.format_exception_only(type(te), te)), lang="py")
            )
