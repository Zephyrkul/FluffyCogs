import asyncio
from copy import copy
from typing import TypeVar

import discord

from .channel import InterChannel

_TT = TypeVar("_TT", bound=type)


def __step(*args, **kwargs):
    # ensure the coro still yields to the event loop
    return asyncio.sleep(0)


def neuter_coros(cls: _TT) -> _TT:
    for name in dir(cls):
        if name in cls.__dict__:
            continue
        if (attr := getattr(cls, name, None)) is None:
            continue
        if asyncio.iscoroutinefunction(attr):
            setattr(cls, name, property(lambda self: __step))
    return cls


@neuter_coros
class InterMessage(discord.Message):
    __slots__ = ()

    @classmethod
    def _from_interaction(cls, interaction: discord.Interaction, prefix: str) -> "InterMessage":
        assert interaction.data
        self = InterMessage.__new__(InterMessage)

        self._state = interaction._state
        self._edited_timestamp = None

        self.tts = False
        self.webhook_id = None
        self.mention_everyone = False
        self.embeds = []
        self.role_mentions = []
        self.id = interaction.id
        self.author = interaction.user
        self.nonce = None
        self.pinned = False
        self.type = discord.MessageType.default
        self.flags = discord.MessageFlags()
        self.reactions = []
        self.reference = None
        self.application = None
        self.activity = None
        self.stickers = []
        self.components = []
        self.role_subscription = None
        self.application_id = None
        self.position = None

        if not interaction.channel:
            raise RuntimeError("Interaction channel is missing, maybe a Discord bug")
        self.channel = copy(interaction.channel)  # type: ignore
        self.channel.__class__ = type(
            InterChannel.__name__, (InterChannel, self.channel.__class__), {"__slots__": ()}
        )

        guild = self.guild = interaction.guild or self.channel.guild
        if guild and not guild.me:
            # forcibly populate guild.me
            guild._add_member(
                discord.Member(
                    data={
                        "roles": [],
                        "user": interaction.client.user._to_minimal_user_json(),
                        "flags": 0,
                    },
                    guild=guild,
                    state=interaction._state,
                )
            )

        self._recreate_from_interaction(interaction, prefix)
        return self

    def _recreate_from_interaction(self, interaction: discord.Interaction, prefix: str):
        assert interaction.data and interaction.client.user

        self.content = f"{prefix}{interaction.namespace.command}"
        if interaction.namespace.arguments:
            self.content = f"{self.content} {interaction.namespace.arguments}"
        if interaction.namespace.attachment:
            self.attachments = [interaction.namespace.attachment]
        else:
            self.attachments = []

        resolved = interaction.data.get("resolved", {})
        if self.guild:
            self.mentions = [
                discord.Member(data=user_data, guild=self.guild, state=self._state)
                for user_data in resolved.get("members", {}).values()
            ]
        else:
            self.mentions = [
                discord.User(data=user_data, state=self._state)
                for user_data in resolved.get("users", {}).values()
            ]

    def to_reference(self, *, fail_if_not_exists: bool = True):
        return None

    def to_message_reference_dict(self):
        return discord.utils.MISSING

    async def reply(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)

    def edit(self, *args, **kwargs):
        return asyncio.sleep(0, self)
