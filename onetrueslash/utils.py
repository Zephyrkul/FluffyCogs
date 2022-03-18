from contextvars import ContextVar
from typing import TYPE_CHECKING, Generator, Optional, Protocol

from redbot.core import commands

if TYPE_CHECKING:
    from .context import InterContext


contexts = ContextVar["InterContext"]("contexts")


class SupportsTyping(Protocol):
    async def trigger_typing(self) -> None:
        ...


class Thinking:
    def __init__(self, destination: SupportsTyping):
        self.destination = destination

    async def __aenter__(self):
        await self.destination.trigger_typing()

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
