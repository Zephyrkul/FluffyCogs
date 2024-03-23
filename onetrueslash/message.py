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

    def __init__(self, **kwargs) -> None:
        raise RuntimeError

    @classmethod
    def _from_interaction(cls, interaction: discord.Interaction, prefix: str) -> "InterMessage":
        assert interaction.data
        assert interaction.client.user

        self = InterMessage.__new__(InterMessage)
        self._state = interaction._state
        self._edited_timestamp = None

        self.tts = False
        self.webhook_id = None
        self.mention_everyone = False
        self.embeds = []
        self.role_mentions = []
        self.id = interaction.id
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

        channel = interaction.channel
        if not channel:
            raise RuntimeError("Interaction channel is missing, maybe a Discord bug")

        self.guild = interaction.guild
        if interaction.guild_id and not interaction.guild:
            # act as if this is a DMChannel
            assert isinstance(interaction.user, discord.Member)
            self.author = interaction.user._user
            channel = discord.DMChannel(
                me=interaction.client.user,
                state=interaction._state,
                data={
                    "id": channel.id,
                    "name": str(channel),
                    "type": 1,
                    "last_message_id": None,
                    "recipients": [
                        self.author._to_minimal_user_json(),
                        interaction.client.user._to_minimal_user_json(),
                    ],
                },  # type: ignore
            )
        else:
            self.author = interaction.user
            channel = copy(channel)

        channel.__class__ = type(
            InterChannel.__name__, (InterChannel, channel.__class__), {"__slots__": ()}
        )
        self.channel = channel  # type: ignore

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
