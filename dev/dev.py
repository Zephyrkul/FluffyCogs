import __future__

import ast
import asyncio
import builtins
import contextlib
import functools
import importlib
import inspect
import io
import itertools
import logging
import sys
import textwrap
import types
from contextvars import ContextVar
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar
from typing_extensions import ParamSpec
from weakref import WeakSet

import discord
import rich
from pygments.styles import get_style_by_name
from redbot.core import commands, dev_commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.predicates import MessagePredicate

# From stdlib's codeop
_features: List[__future__._Feature] = [
    getattr(__future__, fname) for fname in __future__.all_feature_names
]

logger = logging.getLogger("red.fluffy.dev")
_: Callable[[str], str] = dev_commands._
ctxconsole = ContextVar[rich.console.Console]("ctxconsole")
T = TypeVar("T")
P = ParamSpec("P")


class SolarizedCustom(get_style_by_name("solarized-dark")):
    background_color = None
    line_number_background_color = None


def log_exceptions(func: Callable[P, Any]) -> Callable[P, Any]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("Exception in function %s", func.__name__)
            raise

    return wrapper


@contextlib.asynccontextmanager
async def redirect(**kwargs):
    if "file" not in kwargs:
        kwargs["file"] = file = io.StringIO()
    else:
        file = None
    console = rich.console.Console(**kwargs)
    token = ctxconsole.set(console)
    try:
        yield console
    finally:
        ctxconsole.reset(token)
        if file:
            file.close()


class Patcher(Generic[T, P]):
    patchers: "WeakSet[Patcher[T, P]]" = WeakSet()

    def __new__(cls, original: Callable[P, T], new: Callable[P, T]):
        if isinstance(new, cls):
            new = new.new
        if isinstance(original, cls):
            # just redirect it
            original.new = new
            return original
        return super().__new__(cls)

    def __init__(self, original: Callable[P, T], new: Callable[P, T]):
        self.original = original
        self.new = new

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return self.new(*args, **kwargs)
        except Exception:
            return self.original(*args, **kwargs)

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._original, attr)

    @property
    def original(self) -> Callable[P, T]:
        original = self._original
        try:
            self.__code
        except AttributeError:
            return original
        else:
            assert isinstance(original, types.FunctionType)
            func = types.FunctionType(
                self.__code,
                original.__globals__,
                argdefs=original.__defaults__,
                closure=original.__closure__,
            )
            if self.__kwdefaults is not None:
                func.__kwdefaults__ = self.__kwdefaults
            return func

    @original.setter
    def original(self, original: Callable[P, T]) -> None:
        if self in self.patchers:
            raise RuntimeError("Cannot change original after patching")
        try:
            del self.__code
            del self.__kwdefaults
        except AttributeError:
            pass
        if hasattr(original, "__code__"):
            self.__code = original.__code__
            self.__kwdefaults = original.__kwdefaults__
        elif not hasattr(original, "__self__"):
            raise TypeError(f"Unsupported type for original: {type(original)}")
        self._original = original

    def patch(self) -> None:
        original = self._original
        try:
            self.__code
        except AttributeError:
            setattr(original.__self__, original.__name__, self)
        else:
            # this is the second dumbest code I've ever written

            def caller(*args: P.args, __self: "Patcher[T, P]" = self, **kwargs: P.kwargs) -> T:
                return __self(*args, **kwargs)

            original.__code__ = caller.__code__
            original.__kwdefaults__ = {**(original.__kwdefaults__ or {}), **caller.__kwdefaults__}
        self.patchers.add(self)

    def unpatch(self):
        original = self._original
        try:
            self.__code
        except AttributeError:
            setattr(original.__self__, original.__name__, original)
        else:
            original.__code__ = self.__code
            if self.__kwdefaults is not None:
                original.__kwdefaults__ = self.__kwdefaults
            else:
                del original.__kwdefaults__
        self.patchers.remove(self)

    def __repr__(self) -> str:
        return repr(self._original)


def _displayhook(obj: Any) -> None:
    if obj is not None:
        _console = ctxconsole.get()
        builtins._ = None
        rich.pretty.pprint(obj, console=_console)
        builtins._ = obj


def _get_console() -> rich.console.Console:
    return ctxconsole.get()


def patch_hooks():
    # monkeypatching is 👌
    Patcher(sys.displayhook, _displayhook).patch()
    Patcher(rich.get_console, _get_console).patch()


def reset_hooks():
    try:
        for patched in list(Patcher.patchers):
            logger.debug("Unpatching: %r", patched)
            patched.unpatch()
    except Exception:
        logger.critical(
            "Error resetting hooks - please report this error and restart your bot", exc_info=True
        )
    else:
        logger.debug("Hooks reset successfully")


class Exit(BaseException):
    pass


# This is taken straight from stdlib's codeop,
# but with some modifications for this usecase
class Compiler:
    default_flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT

    def __init__(self, flags: int = 0):
        self.flags = self.default_flags | flags

    def __call__(self, source, filename, mode, flags: int = 0):
        codeob = compile(source, filename, mode, self.flags | flags, 1, 0)
        try:
            co_flags = codeob.co_flags
        except AttributeError:
            pass
        else:
            for feature in _features:
                if co_flags & feature.compiler_flag:
                    self.flags |= feature.compiler_flag
        return codeob


class Env(Dict[str, Any]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imported: List[str] = []

    def __missing__(self, key: str):
        if key in ("exit", "quit"):
            raise Exit()
        try:
            # this is called implicitly after KeyError, but
            # some modules would overwrite builtins (e.g. bin)
            return getattr(builtins, key)
        except AttributeError:
            pass
        try:
            module = importlib.import_module(key)
        except ImportError:
            pass
        else:
            self.imported.append(key)
            self[key] = module
            return module
        try:
            if cog := self["bot"].get_cog(key):
                return cog
        except (AttributeError, KeyError):
            pass
        raise KeyError(key)

    def get_formatted_imports(self) -> str:
        if not (imported := self.imported):
            return ""
        imported.sort()
        message = "".join(f">>> import {import_}\n" for import_ in imported)
        imported.clear()
        return message


class Dev(dev_commands.Dev):
    """Various development focused utilities."""

    _last_result: Any
    sessions: Dict[int, bool]
    env_extensions: Dict[str, Callable[[commands.Context], Any]]

    def __init__(self, bot: Red):
        self.bot = bot
        super().__init__()

    @log_exceptions
    def cog_unload(self) -> None:
        self.sessions.clear()
        core_dev = dev_commands.Dev()
        core_dev.env_extensions = self.env_extensions
        self.bot.add_cog(core_dev)

    async def my_exec(self, ctx: commands.Context, *args, **kwargs) -> bool:
        tasks: List[asyncio.Task] = [
            asyncio.create_task(
                ctx.bot.wait_for("message", check=MessagePredicate.cancelled(ctx))
            ),
            asyncio.create_task(self._my_exec(ctx, *args, **kwargs)),
        ]
        async with ctx.typing():
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        result = done.pop().result()
        if isinstance(result, bool):
            return result
        # wait_for finished
        # do nothing
        return False

    async def _my_exec(
        self,
        ctx: commands.Context,
        source: str,
        env: Env,
        compiler: Optional[Compiler] = None,
        **environ: Any,
    ) -> bool:
        assert ctx.invoked_with
        compiler = compiler or Compiler()
        is_alias = False
        if ctx.command is not self.repl:
            if original_message := discord.utils.get(ctx.bot.cached_messages, id=ctx.message.id):
                is_alias = not original_message.content.startswith(ctx.prefix + ctx.invoked_with)
        message = environ.get("message", ctx.message)
        env.update(environ)
        exited = False
        filename = f"<{ctx.invoked_with}>"

        if isinstance(ctx.author, discord.Member):
            mobile = ctx.author.is_on_mobile()
        else:
            mobile = next(
                filter(
                    None,
                    map(discord.Guild.get_member, ctx.bot.guilds, itertools.repeat(ctx.author.id)),
                )
            ).is_on_mobile()

        kwargs: Dict[str, Any] = {
            "width": 37 if mobile else 80,
            "no_color": mobile,
            "color_system": "auto" if mobile else "standard",
            "tab_size": 2,
            "soft_wrap": False,
        }
        if _console_custom := env.get("_console_custom"):
            try:
                kwargs.update(_console_custom)
            except Exception:
                logger.exception("Error updating console kwargs: falling back to default values")

        async with redirect(**kwargs) as console:
            assert isinstance(console.file, io.StringIO)
            try:
                if source.startswith("from __future__ import"):
                    self.handle_future(source, compiler)
                if ctx.command is self._eval:
                    await self._eval_exec(source, env, filename, compiler)
                else:
                    await self._debug_exec(source, env, filename, compiler)
            except (Exit, SystemExit):
                exited = True
            except KeyboardInterrupt:
                raise
            except:
                self._output_exception(ctx, console, filename)
            if is_alias:
                output = console.file.getvalue()
            else:
                with console.capture() as captured:
                    console.print(
                        rich.syntax.Syntax(
                            env.get_formatted_imports(), "pycon", theme=SolarizedCustom
                        )
                    )
                output = captured.get() + console.file.getvalue()
        asyncio.ensure_future(
            self.send_interactive(
                ctx, output.strip(), message, box_lang="py" if mobile else "ansi"
            )
        )
        return exited

    def _output_exception(
        self, ctx: commands.Context, console: rich.console.Console, filename: str
    ) -> None:
        exc_type, e, tb = sys.exc_info()
        # return only frames that are part of provided code
        while tb:
            if tb.tb_frame.f_code.co_filename == filename:
                break
            tb = tb.tb_next
        if tb and ctx.command is self._eval:
            tb = tb.tb_next or tb  # skip the func() frame if we can
        rich_tb = rich.traceback.Traceback.from_exception(
            exc_type, e, tb, extra_lines=1, theme=SolarizedCustom
        )
        console.print(rich_tb)

    async def _debug_exec(self, source: str, env: Env, filename: str, compiler: Compiler) -> None:
        tree: ast.Module = compiler(source, filename, "exec", flags=ast.PyCF_ONLY_AST)
        if header := tree.body[:-1]:
            body = ast.Module(header, tree.type_ignores)
            compiled = compiler(body, filename, "exec")
            await self.maybe_await(types.FunctionType(compiled, env)())
        node = tree.body[-1]
        if isinstance(node, ast.Expr):
            compiled = compiler(ast.Expression(node.value), filename, "eval")
        else:
            # theoretically, the entire module body could just be thrown here
            # but that would not be backwards compatible with core dev
            compiled = compiler(ast.Interactive([node]), filename, "single")
        await self.maybe_await(types.FunctionType(compiled, env)())

    async def _eval_exec(self, source: str, env: Env, filename: str, compiler: Compiler) -> None:
        source = "async def func():\n" + textwrap.indent(source, "  ")
        compiled = compiler(source, filename, "exec")
        # this Function will never be a coroutine
        types.FunctionType(compiled, env)()

        await self.maybe_await(env["func"]())

    async def send_interactive(
        self,
        ctx: commands.Context,
        output: str,
        message: Optional[discord.Message] = None,
        box_lang: str = "",
    ) -> None:
        # \u02CB = modifier letter grave accent
        output = output and self.sanitize_output(ctx, output).replace("```", "\u02CB\u02CB\u02CB")
        message = message or ctx.message
        assert message.channel == ctx.channel
        try:
            if output:
                await ctx.send_interactive(self.get_pages(output), box_lang=box_lang)
            else:
                if ctx.channel.permissions_for(ctx.me).add_reactions:
                    with contextlib.suppress(discord.HTTPException):
                        await message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
                        return
                await ctx.send("Done.")
        except discord.Forbidden:
            # if this is repl, stop it
            self.sessions.pop(ctx.channel.id, None)

    @staticmethod
    def get_pages(msg: str):
        """Pagify the given message for output to the user."""
        return pagify(msg, delims=["\n", " "], priority=True, shorten_by=12)

    @staticmethod
    async def maybe_await(coro: Any, *, hook: Callable[[Any], None] = _displayhook) -> None:
        if coro is None:
            return
        if inspect.isasyncgen(coro):
            async for result in coro:
                hook(result)
        elif inspect.isawaitable(coro):
            hook(await coro)
        else:
            hook(coro)

    def cleanup_code(self, code: str) -> str:
        code = super().cleanup_code(code)
        # also from stdlib's codeop
        # compiling nocode with "eval" does weird things
        with io.StringIO(code) as codeio:
            for line in codeio:
                line = line.strip()
                if line and not line.startswith("#"):
                    break
            else:
                return "pass"
        return code

    def get_environment(self, ctx: commands.Context) -> Env:
        base_env = super().get_environment(ctx)
        del base_env["_"]
        env = Env(
            {
                "me": ctx.me,
                # redirect builtin console functions to rich
                "print": rich.print,
                "help": functools.partial(rich.inspect, help=True),
                # eval and exec automatically put this in, but types.FunctionType does not
                "__builtins__": builtins,
                # fill in various other environment keys that some code might expect
                "__builtin__": builtins,
                "__doc__": ctx.command.help,
                "__package__": None,
                "__loader__": None,
                "__spec__": None,
            }
        )
        env.update(base_env)
        return env

    @commands.command()
    @commands.is_owner()
    @discord.utils.copy_doc(dev_commands.Dev.debug.callback)
    async def debug(self, ctx: commands.Context, *, code: str):
        env = self.get_environment(ctx)
        code = self.cleanup_code(code)

        await self.my_exec(ctx, code, env)

    @commands.command(name="eval")
    @commands.is_owner()
    @discord.utils.copy_doc(dev_commands.Dev._eval.callback)
    async def _eval(self, ctx, *, body: str):
        env = self.get_environment(ctx)
        body = self.cleanup_code(body)

        await self.my_exec(ctx, body, env)

    @staticmethod
    def handle_future(code: str, compiler: Compiler) -> str:
        # TODO: Maybe compile out future imports using ast?
        exc = None
        lines = code.splitlines(keepends=True)
        for i in range(len(lines)):
            try:
                compiler("".join(lines[: i + 1]), "<future>", "exec")
            except SyntaxError as e:
                if e.msg != "unexpected EOF while parsing":
                    raise
                exc = e
            else:
                return "".join(lines[i + 1 :])
        # it couldn't be compiled out for whatever reason
        raise exc or AssertionError("\N{THINKING FACE} how did this happen")

    @commands.group(invoke_without_command=True)
    @commands.is_owner()
    @discord.utils.copy_doc(dev_commands.Dev.repl.callback)
    async def repl(self, ctx: commands.Context):
        if ctx.channel.id in self.sessions:
            if self.sessions[ctx.channel.id]:
                await ctx.send(
                    _("Already running a REPL session in this channel. Exit it with `quit`.")
                )
            else:
                await ctx.send(
                    _(
                        "Already running a REPL session in this channel. Resume the REPL with `{}repl resume`."
                    ).format(ctx.clean_prefix)
                )
            return

        variables = self.get_environment(ctx)
        compiler = Compiler()

        self.sessions[ctx.channel.id] = True
        await ctx.send(
            _(
                "Enter code to execute or evaluate. `exit` or `quit` to exit. `{}repl pause` to pause."
            ).format(ctx.clean_prefix)
        )

        while True:
            response = await ctx.bot.wait_for("message", check=MessagePredicate.regex(r"^`", ctx))

            if ctx.channel.id not in self.sessions:
                return
            if not self.sessions[ctx.channel.id]:
                continue

            cleaned = self.cleanup_code(response.content)

            exited = await self.my_exec(
                ctx,
                cleaned,
                variables,
                compiler=compiler,
                message=response,
            )

            if exited:
                del self.sessions[ctx.channel.id]
                await ctx.send(_("Exiting."))
                return
