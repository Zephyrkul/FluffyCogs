import inspect
import types
from copy import copy
from typing import Optional, Type, Union

import discord
from discord.ext.commands.view import StringView
from redbot.core import commands
from redbot.core.bot import Red

from .message import InterMessage
from .utils import Thinking, contexts

INCOMPATABLE_PARAMETERS_DISCARD = tuple(
    k
    for k in inspect.signature(discord.abc.Messageable.send).parameters
    if k not in inspect.signature(discord.Webhook.send).parameters
)


class InterContext(commands.Context):
    _deferring: bool = False
    _ticked: Optional[str] = None
    _interaction: discord.Interaction[Red]
    message: InterMessage

    @classmethod
    def _get_type(cls, bot: Red) -> Type["InterContext"]:
        default = bot.get_context.__kwdefaults__.get("cls", None)
        if not isinstance(default, type) or default in cls.__mro__:
            return cls
        try:
            return types.new_class(cls.__name__, (cls, default))
        except Exception:
            return cls

    @classmethod
    async def from_interaction(
        cls: Type["InterContext"],
        interaction: discord.Interaction[Red],
        *,
        recreate_message: bool = False,
    ) -> "InterContext":
        prefix = f"</{interaction.data['name']}:{interaction.data['id']}> command:"
        try:
            self = contexts.get()
            if recreate_message:
                assert self.prefix is not None
                self.message._recreate_from_interaction(interaction, prefix)
                view = self.view = StringView(self.message.content)
                view.skip_string(self.prefix)
                invoker = view.get_word()
                self.invoked_with = invoker
                self.command = interaction.client.all_commands.get(invoker)
            return self
        except LookupError:
            pass
        message = InterMessage._from_interaction(interaction, prefix)
        view = StringView(message.content)
        view.skip_string(prefix)
        invoker = view.get_word()
        self = cls._get_type(interaction.client)(
            message=message,
            prefix=prefix,
            bot=interaction.client,
            view=view,
            invoked_with=invoker,
            command=interaction.client.all_commands.get(invoker),
        )
        # don't set self.interaction so make d.py parses commands the old way
        self._interaction = interaction
        interaction._baton = self
        contexts.set(self)
        return self

    @property
    def clean_prefix(self) -> str:
        return f"/{self._interaction.data['name']} command:"

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

    async def send(self, *args, **kwargs):
        interaction = self._interaction
        if interaction.is_expired():
            assert interaction.channel
            return await interaction.channel.send(*args, **kwargs)  # type: ignore
        await self.typing(ephemeral=True)
        self._deferring = False
        delete_after = kwargs.pop("delete_after", None)
        for key in INCOMPATABLE_PARAMETERS_DISCARD:
            kwargs.pop(key, None)
        m = await interaction.followup.send(*args, **kwargs)
        if delete_after:
            await m.delete(delay=delete_after)
        return m

    def typing(self, *, ephemeral: bool = False) -> Thinking:
        return Thinking(self, ephemeral=ephemeral)

    async def defer(self, *, ephemeral: bool = False) -> None:
        await self._interaction.response.defer(ephemeral=ephemeral)

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
            command.usage = f"arguments:{signature}"
        return await super().send_help(command)

    def _apply_implicit_permissions(
        self, user: discord.abc.User, base: discord.Permissions
    ) -> discord.Permissions:
        if base.administrator or (self.guild and self.guild.owner_id == user.id):
            return discord.Permissions.all()

        base = copy(base)
        if not base.send_messages:
            base.send_tts_messages = False
            base.mention_everyone = False
            base.embed_links = False
            base.attach_files = False

        if not base.read_messages:
            base &= ~discord.Permissions.all_channel()

        channel_type = self.channel.type
        if channel_type in (discord.ChannelType.voice, discord.ChannelType.stage_voice):
            if not base.connect:
                denied = discord.Permissions.voice()
                denied.update(manage_channels=True, manage_roles=True)
                base &= ~denied
        else:
            base &= ~discord.Permissions.voice()

        return base

    @discord.utils.cached_property
    def permissions(self):
        if self._interaction._permissions == 0:
            return discord.Permissions._dm_permissions()  # type: ignore
        return self._apply_implicit_permissions(self.author, self._interaction.permissions)

    @discord.utils.cached_property
    def bot_permissions(self):
        return self._apply_implicit_permissions(
            self.me, self._interaction.app_permissions
        ) | discord.Permissions(send_messages=True, attach_files=True, embed_links=True)
