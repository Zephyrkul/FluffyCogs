import asyncio
import bisect
import builtins
import contextlib
import importlib
import inspect
import io
import textwrap
import traceback
import types
from copy import copy
from typing import Any, Dict, List, Optional

import discord
from redbot.core import commands, dev_commands
from redbot.core.utils.predicates import MessagePredicate

_ = dev_commands._


class Env(Dict[str, Any]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imported = []

    @classmethod
    def from_context(cls, ctx: commands.Context, **kwargs: Any) -> "Env":
        self = cls(
            {
                # "_": None,  # let __builtins__ handle this one
                "ctx": ctx,
                "bot": ctx.bot,
                "message": ctx.message,
                "guild": ctx.guild,
                "channel": ctx.channel,
                "author": ctx.author,
                "asyncio": asyncio,  # not including this can cause errors with async-compile
                "__name__": "__main__",  # not including this can cause errors with typing (#3648)
                # eval and exec automatically put this in, but types.FunctionType does not
                "__builtins__": builtins,
                # fill in various other environment keys that some code might expect
                "__builtin__": builtins,
                "__doc__": inspect.cleandoc(ctx.cog.__doc__),
                "__package__": None,
                "__loader__": None,
                "__spec__": None,
            },
            **kwargs,
        )
        return self

    def __missing__(self, key):
        try:
            module = importlib.import_module(key)
        except ImportError:
            raise KeyError(key) from None
        else:
            bisect.insort(self.imported, key)
            self[key] = module
            return module

    def get_formatted_imports(self) -> Optional[str]:
        if not self.imported:
            return None
        message = "\n".join(map("import {}".format, self.imported))
        self.imported.clear()
        return message


class Dev(dev_commands.Dev):
    """Various development focused utilities."""

    def __init__(self):
        super().__init__()
        del self._last_result  # we don't need this anymore
        self.sessions = {}

    async def my_exec(
        self,
        source,
        ctx: commands.Context,
        env: Dict[str, Any],
        *modes: str,
        run: str = None,
        **environ: Any,
    ) -> Any:
        if not modes:
            modes = ("single",)
        # [Imports, Prints, Errors, Result]
        ret: List[Optional[str]] = [None, None, None, None]
        env.update(environ)
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                exc = None
                for mode in modes:
                    try:
                        compiled = self.async_compile(source, f"<{ctx.command}>", mode)
                        await self.maybe_await(types.FunctionType(compiled, env)())
                    except SyntaxError as e:
                        exc = e
                        continue
                    else:
                        if runner := env.get(run):
                            output = await self.maybe_await(runner())
                            if output is not None:
                                setattr(builtins, "_", output)
                                ret[3] = "# Result:\n" + repr(output)
                        break
                else:
                    if exc:
                        raise exc
        except BaseException:
            ret[2] = "# Exception:\n" + traceback.format_exc()
        if getattr(env, "imported", None):
            assert isinstance(env, Env)
            ret[0] = "# Imported:\n" + str(env.get_formatted_imports())
        printed = stdout.getvalue().strip()
        if printed:
            ret[1] = "# Output:\n" + printed
        asyncio.ensure_future(self.send_interactive(ctx, *ret))
        return getattr(builtins, "_", None)

    async def send_interactive(self, ctx: commands.Context, *items: Optional[str]) -> None:
        try:
            if not any(items):
                if not ctx.channel.permissions_for(ctx.me).add_reactions or not await ctx.tick():
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
        env = Env.from_context(ctx, commands=commands, _=self._last_result)
        code = self.cleanup_code(code)

        await self.my_exec(code, ctx, env, "single")

    @commands.command(name="eval")
    @commands.is_owner()
    async def _eval(self, ctx, *, body: str):
        """Execute asynchronous code.

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
        env = Env.from_context(ctx, commands=commands, _=self._last_result)
        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = "async def func():\n" + textwrap.indent(body, "  ")
        await self.my_exec(to_compile, ctx, env, "exec", run="func")

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

        variables = Env.from_context(ctx, _=self._last_result)

        self.sessions[ctx.channel.id] = True
        await ctx.send(_("Enter code to execute or evaluate. `exit()` or `quit` to exit."))

        while True:
            response = await ctx.bot.wait_for("message", check=MessagePredicate.regex(r"^`", ctx))

            if ctx.channel.id not in self.sessions:
                return
            if not self.sessions[ctx.channel.id]:
                continue

            cleaned = self.cleanup_code(response.content)

            if cleaned.rstrip("()") in ("quit", "exit", "stop"):
                del self.sessions[ctx.channel.id]
                await ctx.send(_("Exiting."))
                return

            await self.my_exec(cleaned, ctx, variables, "single", "exec", message=response)

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
