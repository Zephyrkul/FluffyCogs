from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Generator, Optional

# discord.ext.commands.GroupMixin has easier typehints to work with
from discord.ext.commands import GroupMixin
from redbot.core import commands

if TYPE_CHECKING:
    from .context import InterContext


try:
    import regex as re
except ImportError:
    import re


contexts = ContextVar["InterContext"]("contexts")


def valid_app_name(name: str) -> str:
    from discord.app_commands.commands import VALID_SLASH_COMMAND_NAME, validate_name

    name = "_".join(re.findall(VALID_SLASH_COMMAND_NAME.pattern.strip("^$"), name.lower()))
    return validate_name(name[:32])


class Thinking:
    def __init__(self, ctx: "InterContext", *, ephemeral: bool = False):
        self.ctx = ctx
        self.ephemeral = ephemeral

    def __await__(self) -> Generator[Any, Any, None]:
        ctx = self.ctx
        interaction = ctx._interaction
        if not ctx._deferring and not interaction.response.is_done():
            # yield from is necessary here to force this function to be a generator
            # even in the negative case
            ctx._deferring = True
            return (yield from interaction.response.defer(ephemeral=self.ephemeral).__await__())

    async def __aenter__(self):
        await self

    async def __aexit__(self, *args):
        pass


def walk_aliases(
    group: GroupMixin[Any], /, *, parent: Optional[str] = "", show_hidden: bool = False
) -> Generator[str, None, None]:
    for name, command in group.all_commands.items():
        if command.qualified_name == "help":
            continue
        if not command.enabled or (not show_hidden and command.hidden):
            continue
        yield f"{parent}{name}"
        if isinstance(command, commands.GroupMixin):
            yield from walk_aliases(command, parent=f"{parent}{name} ", show_hidden=show_hidden)
