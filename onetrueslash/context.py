from typing import Optional, Type

import discord
from discord.ext import commands as dpy_commands
from redbot.core import commands
from redbot.core.bot import Red

from .channel import InterChannel
from .message import InterMessage
from .utils import contexts


class InterContext(InterChannel, commands.Context):
    _deferred: bool
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
                view = self.view = dpy_commands.view.StringView(self.message.content)
                view.skip_string(self.prefix)
                invoker = view.get_word()
                self.invoked_with = invoker
                self.command = interaction.client.all_commands.get(invoker)
            return self
        except LookupError:
            pass
        message = await InterMessage.from_interaction(interaction)
        prefix = f"/{interaction.data['name']} "
        view = dpy_commands.view.StringView(message.content)
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
        self._deferred = False
        contexts.set(self)
        return self

    async def tick(self, *, message: Optional[str] = None) -> bool:
        return False
