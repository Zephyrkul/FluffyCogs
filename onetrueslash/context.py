from copy import copy
from typing import Optional, Type, Union

import discord
from discord.ext.commands.view import StringView
from redbot.core import commands
from redbot.core.bot import Red

from .channel import InterChannel
from .message import InterMessage
from .utils import contexts


class InterContext(InterChannel, commands.Context):
    _deferring: bool = False
    _ticked: Optional[str] = None
    _first_response: int = 0
    interaction: discord.Interaction
    message: InterMessage

    @classmethod
    async def from_interaction(
        cls: Type["InterContext"],
        interaction: discord.Interaction,
        *,
        recreate_message: bool = False,
    ) -> "InterContext":
        assert isinstance(interaction.client, Red)
        try:
            self = contexts.get()
            if recreate_message:
                assert self.prefix is not None
                self.message.recreate_from_interaction(interaction)
                view = self.view = StringView(self.message.content)
                view.skip_string(self.prefix)
                invoker = view.get_word()
                self.invoked_with = invoker
                self.command = interaction.client.all_commands.get(invoker)
            return self
        except LookupError:
            pass
        message = await InterMessage.from_interaction(interaction)
        prefix = f"/{interaction.data['name']} command: "
        view = StringView(message.content)
        view.skip_string(prefix)
        invoker = view.get_word()
        self = cls(
            message=message,
            prefix=prefix,
            bot=interaction.client,
            view=view,
            invoked_with=invoker,
            command=interaction.client.all_commands.get(invoker),
        )
        self.interaction = interaction
        contexts.set(self)
        return self

    async def tick(self, *, message: Optional[str] = None) -> bool:
        return await super().tick(message="Done." if message is None else message)

    async def react_quietly(
        self,
        reaction: Union[discord.Emoji, discord.Reaction, discord.PartialEmoji, str],
        *,
        message: Optional[str] = None,
    ) -> bool:
        self._ticked = f"{reaction} {message}" if message else str(reaction)
        return False

    async def send_help(
        self, command: Optional[Union[commands.Command, commands.GroupMixin, str]] = None
    ):
        command = command or self.command
        if isinstance(command, str):
            command = self.bot.get_command(command) or command
        signature: str
        if signature := getattr(command, "signature", ""):
            assert not isinstance(command, str)
            command = copy(command)
            command.usage = f"arguments: {signature}"
        return await super().send_help(command)
