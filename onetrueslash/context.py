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
        prefix = f"/{interaction.data['name']} "
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

    async def react_quietly(
        self,
        reaction: Union[discord.Emoji, discord.Reaction, discord.PartialEmoji, str],
        *,
        message: Optional[str] = None,
    ) -> bool:
        message = message or "Done."
        self._ticked = f"{reaction} {message}"
        return False
