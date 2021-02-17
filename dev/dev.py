import __future__

import ast
import asyncio
import builtins
import contextlib
import importlib
import inspect
import io
import sys
import textwrap
import traceback
import types
from copy import copy
from itertools import chain
from typing import Any, Dict, List, Optional

import discord
from redbot.core import commands, dev_commands
from redbot.core.utils.predicates import MessagePredicate

# From stdlib's codeop
_features = [getattr(__future__, fname) for fname in __future__.all_feature_names]

_ = dev_commands._


# This is taken straight from stdlib's codeop,
# but with some modifications for this usecase
class Compiler:
    default_flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT

    def __init__(self, flags: int = 0):
        self.flags = self.default_flags | flags

    def __call__(self, source, filename, mode, flags: int = 0):
        self.flags |= flags
        codeob = compile(source, filename, mode, self.flags, 1, 0)
        for feature in _features:
            if codeob.co_flags & feature.compiler_flag:
                self.flags |= feature.compiler_flag
        return codeob


class Env(Dict[str, Any]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imported = []

    @classmethod
    def from_context(cls, ctx: commands.Context, /, **kwargs: Any) -> "Env":
        self = cls(
            {
                # "_": None,  # let __builtins__ handle this one
                "ctx": ctx,
                "author": ctx.author,
                "bot": ctx.bot,
                "channel": ctx.channel,
                "guild": ctx.guild,
                "me": ctx.me,
                "message": ctx.message,
                "asyncio": asyncio,  # not including this can cause errors with async-compile
                "discord": discord,  # not necessary, but people generally assume this
                "__name__": "__main__",  # not including this can cause errors with typing (#3648)
                # eval and exec automatically put this in, but types.FunctionType does not
                "__builtins__": builtins,
                # fill in various other environment keys that some code might expect
                "__builtin__": builtins,
                "__doc__": ctx.command.help,
                "__package__": None,
                "__loader__": None,
                "__spec__": None,
            },
            **kwargs,
        )
        return self

    def __missing__(self, key):
        try:
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
        dotkey = "." + key
        for tlms in ["redbot", "discord"]:
            modules = [
                v for k, v in sys.modules.items() if k.endswith(dotkey) and k.startswith(tlms)
            ]
            if len(modules) == 1:
                module = modules[0]
                self.imported.append(f"{module.__name__} as {key}")
                self[key] = module
                return module
        raise KeyError(key)

    def get_formatted_imports(self) -> Optional[str]:
        if not self.imported:
            return None
        self.imported.sort()
        message = "\n".join(map("import {}".format, self.imported))
        self.imported.clear()
        return message


class Dev(dev_commands.Dev):
    """Various development focused utilities."""

    # Schema: [my version] <[targeted bot version]>
    __version__ = "0.0.2 <3.4.1dev>"

    def __init__(self):
        super().__init__()
        del self._last_result  # we don't need this anymore
        self.sessions = {}

    def format_help_for_context(self, ctx: commands.Context) -> str:
        pre = super().format_help_for_context(ctx)
        if pre:
            return f"{pre}\nCog Version: {self.__version__}"
        else:
            return f"Cog Version: {self.__version__}"

    async def my_exec(self, ctx: commands.Context, *args, **kwargs) -> None:
        tasks = [
            ctx.bot.wait_for("message", check=MessagePredicate.cancelled(ctx)),
            self._my_exec(ctx, *args, **kwargs),
        ]
        async with ctx.typing():
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            result = task.result()
            if not result:
                # _my_exec finished
                return
            # wait_for finished
            assert isinstance(result, discord.Message)
            if not ctx.channel.permissions_for(
                ctx.me
            ).add_reactions or not await ctx.react_quietly("\N{CROSS MARK}"):
                await ctx.send("Cancelled.")

    async def _my_exec(
        self,
        ctx: commands.Context,
        source,
        env: Dict[str, Any],
        *modes: str,
        run: str = None,
        compiler: Compiler = None,
        **environ: Any,
    ) -> None:
        compiler = compiler or Compiler()
        original_message = discord.utils.get(ctx.bot.cached_messages, id=ctx.message.id)
        if original_message:
            is_alias = not original_message.content.startswith(ctx.prefix + ctx.invoked_with)
        else:
            is_alias = False
        message = environ.get("message", ctx.message)
        if not modes:
            modes = ("single",)
        # [Imports, Prints, Errors, Result]
        ret: List[Optional[str]] = [None, None, None, None]
        env.update(environ)
        stdout = io.StringIO()
        filename = f"<{ctx.command}>"
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
                exc = None
                for mode in modes:
                    try:
                        compiled = compiler(source, filename, mode)
                        output = await self.maybe_await(types.FunctionType(compiled, env)())
                    except SyntaxError as e:
                        exc = e
                        continue
                    else:
                        if run:
                            # don't call the func here, leave it to maybe_await
                            output = await self.maybe_await(env[run]())
                        if output is not None:
                            setattr(builtins, "_", output)
                            ret[3] = f"# Result:\n{output!r}"
                        break
                else:
                    if exc:
                        raise exc
        except BaseException as e:
            # return only frames that are part of provided code
            i, j = -1, 0
            for j, (frame, _) in enumerate(traceback.walk_tb(e.__traceback__)):
                if i < 0 and frame.f_code.co_filename == filename:
                    i = j
            if i < 0:
                # this shouldn't ever happen but python allows for some weirdness
                limit = 0
            elif run:
                # the func frame isn't needed
                limit = i - j
            else:
                limit = i - j - 1
            tb = e.__traceback__ if limit else None
            ret[2] = "".join(
                chain(["# Exception:\n"], traceback.format_exception(type(e), e, tb, limit))
            )
        # don't export imports on aliases
        if (
            not is_alias
            and (method := getattr(env, "get_formatted_imports", None))
            and (imported := method())
        ):
            ret[0] = f"# Imported:\n{imported}"
        printed = stdout.getvalue().strip()
        if printed:
            ret[1] = "# Output:\n" + printed
        asyncio.ensure_future(self.send_interactive(ctx, *ret, message=message))

    async def send_interactive(
        self,
        ctx: commands.Context,
        *items: Optional[str],
        message: discord.Message = None,
    ) -> None:
        message = message or ctx.message
        if message.channel != ctx.channel:
            raise RuntimeError("\N{THINKING FACE} how did this happen")
        try:
            if not any(items):
                if ctx.channel.permissions_for(ctx.me).add_reactions:
                    with contextlib.suppress(discord.HTTPException):
                        await message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
                        return
                await ctx.send("Done.")
                return
            for item in filter(None, items):
                await ctx.send_interactive(
                    self.get_pages(
                        self.sanitize_output(ctx, item.replace("```", "`\u200b`\u200b`"))
                    ),
                    box_lang="py",
                )
        except discord.Forbidden:
            # if this is repl, stop it
            self.sessions.pop(ctx.channel.id, None)

    @staticmethod
    async def maybe_await(coro):
        if inspect.isasyncgen(coro):
            async for obj in coro:
                if obj is not None:
                    setattr(builtins, "_", obj)
                    print(repr(obj))

        elif inspect.isawaitable(coro):
            return await coro

        elif inspect.isgenerator(coro):
            for obj in coro:
                if obj is not None:
                    setattr(builtins, "_", obj)
                    print(repr(obj))

        else:
            return coro

    @commands.command()
    @commands.is_owner()
    async def debug(self, ctx, *, code):
        """
        Evaluate a statement of python code as if it were entered into a REPL.

        Environment Variables:
            ctx      - command invokation context
            bot      - bot object
            message  - the command's message object
            guild    - the current guild, or None in direct messages
            channel  - the current channel object
            author   - command author's member object
            commands - redbot.core.commands
            _        - The result of the last dev command.
        """
        env = Env.from_context(ctx, commands=commands)
        code = self.cleanup_code(code).strip()

        compiler = Compiler()
        if code.startswith("from __future__ import"):
            try:
                code = self.handle_future(code, compiler)
            except SyntaxError as e:
                await self.send_interactive(
                    ctx,
                    "# Exception:\nTraceback (most recent call last):\n"
                    + "".join(traceback.format_exception_only(type(e), e)),
                )
                return

        await self.my_exec(ctx, code, env, "eval", "single", compiler=compiler)

    @commands.command(name="eval")
    @commands.is_owner()
    async def _eval(self, ctx, *, body: str):
        """
        Execute asynchronous code.

        This command wraps code into the body of an async function and then
        calls and awaits it. The bot will respond with anything printed to
        stdout, as well as the return value of the function.

        The code can be within a codeblock, inline code or neither, as long
        as they are not mixed and they are formatted correctly.

        Environment Variables:
            ctx      - command invokation context
            bot      - bot object
            channel  - the current channel object
            author   - command author's member object
            message  - the command's message object
            commands - redbot.core.commands
            _        - The result of the last dev command.
        """
        env = Env.from_context(ctx, commands=commands)
        body = self.cleanup_code(body).strip()

        compiler = Compiler()
        if body.startswith("from __future__ import"):
            try:
                body = self.handle_future(body, compiler)
            except SyntaxError as e:
                await self.send_interactive(
                    ctx,
                    "# Exception:\nTraceback (most recent call last):\n"
                    + "".join(traceback.format_exception_only(type(e), e)),
                )
                return

        to_compile = "async def func():\n" + textwrap.indent(body, "  ")
        await self.my_exec(ctx, to_compile, env, "exec", compiler=compiler, run="func")

    @staticmethod
    def handle_future(code: str, compiler: Compiler) -> str:
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
        raise exc or RuntimeError("\N{THINKING FACE} how did this happen")

    def cog_unload(self):
        self.sessions.clear()

    @commands.group(invoke_without_command=True)
    @commands.is_owner()
    async def repl(self, ctx):
        """
        Open an interactive REPL.

        The REPL will only recognise code as messages which start with a
        backtick. This includes codeblocks, and as such multiple lines can be
        evaluated.
        """
        if ctx.channel.id in self.sessions:
            await ctx.send(
                _("Already running a REPL session in this channel. Exit it with `quit`.")
            )
            return

        variables = Env.from_context(ctx)
        compiler = Compiler()

        self.sessions[ctx.channel.id] = True
        await ctx.send(_("Enter code to execute or evaluate. `exit()` or `quit` to exit."))

        while True:
            response = await ctx.bot.wait_for("message", check=MessagePredicate.regex(r"^`", ctx))

            if ctx.channel.id not in self.sessions:
                return
            if not self.sessions[ctx.channel.id]:
                continue

            cleaned = self.cleanup_code(response.content).strip()

            if cleaned.rstrip("( )") in ("quit", "exit", "stop"):
                del self.sessions[ctx.channel.id]
                await ctx.send(_("Exiting."))
                return

            if cleaned.startswith("from __future__ import"):
                try:
                    cleaned = self.handle_future(cleaned, compiler)
                except SyntaxError as e:
                    asyncio.ensure_future(
                        self.send_interactive(
                            ctx,
                            "# Exception:\nTraceback (most recent call last):\n"
                            + "".join(traceback.format_exception_only(type(e), e)),
                        )
                    )
                    continue

            await self.my_exec(
                ctx,
                cleaned,
                variables,
                "eval",
                "single",
                "exec",
                compiler=compiler,
                message=response,
            )

    # The below command is unchanged from NeuroAssassin's code.
    # It is present here mainly as a backport for previous versions of Red.
    @repl.command(aliases=["resume"])
    async def pause(self, ctx, toggle: Optional[bool] = None):
        """Pauses/resumes the REPL running in the current channel"""
        if ctx.channel.id not in self.sessions:
            await ctx.send(_("There is no currently running REPL session in this channel."))
            return

        if toggle is None:
            toggle = not self.sessions[ctx.channel.id]
        self.sessions[ctx.channel.id] = toggle

        if toggle:
            await ctx.send(_("The REPL session in this channel has been resumed."))
        else:
            await ctx.send(_("The REPL session in this channel is now paused."))

    @commands.command()
    @commands.is_owner()
    async def mock(self, ctx: commands.Context, user: discord.Member, *, command: str):
        """
        Mock another user invoking a command.

        The prefix must not be entered.
        """
        if user.bot:
            return

        msg = copy(ctx.message)
        msg.author = user
        msg.content = ctx.prefix + command

        new_ctx = await ctx.bot.get_context(msg)
        await ctx.bot.invoke(new_ctx)

    @commands.command(name="mockmsg")
    @commands.is_owner()
    async def mock_msg(self, ctx: commands.Context, user: discord.Member, *, content: str):
        """Dispatch a message event as if it were sent by a different user."""
        msg = copy(ctx.message)
        msg.author = user
        msg.content = content

        ctx.bot.dispatch("message", msg)
