import __future__

import ast
import asyncio
import builtins
import contextlib
import importlib
import inspect
import io
import logging
import sys
import textwrap
import types
from contextvars import ContextVar
from copy import copy
from typing import Any, Callable, Dict, List, TypeVar
from typing_extensions import ParamSpec
from weakref import WeakSet

import discord
import rich
from redbot.core import commands, dev_commands
from redbot.core.utils.predicates import MessagePredicate

# From stdlib's codeop
_features: List[__future__._Feature] = [
    getattr(__future__, fname) for fname in __future__.all_feature_names
]

_: Callable[[str], str] = dev_commands._
ctxconsole = ContextVar[rich.console.Console]("ctxconsole")
T = TypeVar("T")
P = ParamSpec("P")


@contextlib.asynccontextmanager
async def redirect(console: rich.console.Console):
    token = ctxconsole.set(console)
    try:
        yield console
    finally:
        ctxconsole.reset(token)


class Patcher(Callable[P, T]):
    patchers: "WeakSet[Patcher]" = WeakSet()

    def __new__(cls, original: Callable[P, T], new: Callable[P, T], /) -> "Patcher":
        if isinstance(original, cls):
            # just redirect it
            original.new = new
            return original
        return super().__new__(cls)

    def __init__(self, original: Callable[P, T], new: Callable[P, T], /):
        self.original = original
        self.new = new

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return self.new(*args, **kwargs)
        except Exception:
            return self.original(*args, **kwargs)

    def __getattr__(self, attr: str) -> Any:
        return getattr(self.original, attr)

    @classmethod
    def patch(cls, original: Callable[P, T], new: Callable[P, T], /) -> "Patcher":
        self = cls(original, new)
        container = sys.modules[original.__module__]
        for attr in original.__qualname__.split(".")[:-1]:
            container = getattr(original, attr)
        setattr(container, original.__name__, self)
        cls.patchers.add(self)
        return self

    def unpatch(self):
        original = self.original
        container = sys.modules[original.__module__]
        for attr in original.__qualname__.split(".")[:-1]:
            container = getattr(original, attr)
        setattr(container, original.__name__, original)
        self.patchers.discard(self)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(original={self.original!r}, new={self.new!r})"


def _print(*args, **kwargs):
    ctxconsole.get()  # this is here solely to raise an error if there is no redirect
    rich.print(*args, **kwargs)


def _displayhook(obj: Any) -> None:
    if obj is not None:
        builtins._ = None
        rich.print(rich.pretty.pretty_repr(obj))
        builtins._ = obj


def _get_console() -> rich.console.Console:
    return ctxconsole.get()


def patch_hooks():
    # monkeypatching is 👌
    Patcher.patch(builtins.print, _print)
    Patcher.patch(sys.displayhook, _displayhook)
    Patcher.patch(rich.get_console, _get_console)


def reset_hooks():
    logger = logging.getLogger("red.fluffy.dev")
    try:
        for patched in list(Patcher.patchers):
            logger.debug("Unpatching: %r", patched)
            patched.unpatch()
    except Exception:
        logger.critical(
            "Error resetting hooks - please report this error and restart your bot", exc_info=True
        )
    else:
        logger.debug("Hooks reset")


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
        self.imported = []

    def __missing__(self, key):
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
        if not self.imported:
            return ""
        self.imported.sort()
        message = "".join(f">>> import {imported}\n" for imported in self.imported)
        self.imported.clear()
        return message


class Dev(dev_commands.Dev):
    """Various development focused utilities."""

    _last_result: Any
    sessions: Dict[int, bool]
    env_extensions: Dict[str, Callable[[commands.Context], Any]]

    async def my_exec(self, ctx: commands.Context, *args, **kwargs) -> bool:
        message: discord.Message = kwargs.get("message", ctx.message)
        assert message.channel == ctx.channel
        tasks = [
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
        assert isinstance(result, discord.Message)
        if ctx.channel.permissions_for(ctx.me).add_reactions:
            with contextlib.suppress(discord.HTTPException):
                await message.add_reaction("\N{CROSS MARK}")
                return False
        await ctx.send("Cancelled.")
        return False

    async def _my_exec(
        self,
        ctx: commands.Context,
        source: str,
        env: Env,
        compiler: Compiler = None,
        **environ: Any,
    ) -> bool:
        compiler = compiler or Compiler()
        is_alias = False
        if ctx.command is not self.repl:
            if original_message := discord.utils.get(ctx.bot.cached_messages, id=ctx.message.id):
                is_alias = not original_message.content.startswith(ctx.prefix + ctx.invoked_with)
        message = environ.get("message", ctx.message)
        env.update(environ)
        exited = False
        filename = f"<{ctx.invoked_with}>"
        console = rich.console.Console(file=io.StringIO(), width=80)
        try:
            async with redirect(console):
                if source.startswith("from __future__ import"):
                    self.handle_future(source, compiler)
                if ctx.command is self._eval:
                    await self._eval_exec(source, env, filename, compiler)
                else:
                    await self._debug_exec(source, env, filename, compiler)
        except (Exit, SystemExit):
            exited = True
        except BaseException as e:
            # return only frames that are part of provided code
            tb = e.__traceback__
            while tb:
                if tb.tb_frame.f_code.co_filename == filename:
                    break
                tb = tb.tb_next
            if tb and ctx.command is self._eval:
                tb = tb.tb_next  # skip the func() frame
            console.print(
                rich.traceback.Traceback.from_exception(
                    type(e), e, tb or e.__traceback__, extra_lines=0, word_wrap=True
                )
            )
        if is_alias:
            output = console.file.getvalue()
        else:
            output = env.get_formatted_imports() + console.file.getvalue()
        asyncio.ensure_future(self.send_interactive(ctx, output, message))
        return exited

    async def _debug_exec(
        self, source: str, env: Env, filename: str, compiler: Compiler = None
    ) -> None:
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

    async def _eval_exec(
        self, source: str, env: Env, filename: str, compiler: Compiler = None
    ) -> None:
        source = "async def func():\n" + textwrap.indent(source, "  ")
        compiled = compiler(source, filename, "exec")
        # this Function will never be a coroutine
        types.FunctionType(compiled, env)()

        await self.maybe_await(env["func"]())

    @classmethod
    def sanitize_output(cls, ctx: commands.Context, input_: str) -> str:
        # sanitize markdown as well
        # \u02CB = modifier letter grave accent
        return super().sanitize_output(ctx, input_).replace("```", "\u02CB\u02CB\u02CB")

    async def send_interactive(
        self,
        ctx: commands.Context,
        output: str,
        message: discord.Message = None,
    ) -> None:
        message = message or ctx.message
        assert message.channel == ctx.channel
        try:
            if output:
                await ctx.send_interactive(
                    self.get_pages(self.sanitize_output(ctx, output)), box_lang="py"
                )
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
    async def maybe_await(coro, *, hook=_displayhook) -> None:
        if not coro:
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
        for line in code.splitlines():
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

    def cog_unload(self):
        self.sessions.clear()

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

    @commands.command()
    @commands.is_owner()
    @discord.utils.copy_doc(dev_commands.Dev.mock.callback)
    async def mock(self, ctx: commands.Context, user: discord.Member, *, command: str):
        if user.bot:
            return

        msg = copy(ctx.message)
        msg.author = user
        msg.content = ctx.prefix + command

        new_ctx = await ctx.bot.get_context(msg)
        await ctx.bot.invoke(new_ctx)

    @commands.command(name="mockmsg")
    @commands.is_owner()
    @discord.utils.copy_doc(dev_commands.Dev.mock_msg.callback)
    async def mock_msg(self, ctx: commands.Context, user: discord.Member, *, content: str):
        msg = copy(ctx.message)
        msg.author = user
        msg.content = content

        ctx.bot.dispatch("message", msg)
