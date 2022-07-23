from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Generator, Optional

from redbot.core import commands

if TYPE_CHECKING:
    from .context import InterContext


contexts = ContextVar["InterContext"]("contexts")


def valid_app_name(name: str) -> str:
    from discord.app_commands.commands import VALID_SLASH_COMMAND_NAME, validate_name

    name = "_".join(VALID_SLASH_COMMAND_NAME.findall(name.lower()))
    return validate_name(name)


class Thinking:
    def __init__(self, *, ephemeral: bool = False):
        self.ephemeral = ephemeral

    def __await__(self) -> Generator[Any, Any, None]:
        ctx = contexts.get()
        interaction = ctx.interaction
        if not ctx._deferring and not interaction.response.is_done():
            # yield from is necessary here to force this function to be a generator
            # even in the negative case
            ctx._deferring = True
            return (
                yield from ctx.interaction.response.defer(ephemeral=self.ephemeral).__await__()
            )

    async def __aenter__(self):
        await self

    async def __aexit__(self, *args):
        pass


def walk_aliases(
    group: commands.GroupMixin, /, *, parent: Optional[str] = "", show_hidden: bool = False
) -> Generator[str, None, None]:
    name: str
    command: commands.Command
    for name, command in group.all_commands.items():
        if not command.enabled or (not show_hidden and command.hidden):
            continue
        yield f"{parent}{name}"
        if isinstance(command, commands.GroupMixin):
            yield from walk_aliases(command, parent=f"{parent}{name} ", show_hidden=show_hidden)
