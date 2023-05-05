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
    def from_interaction(cls, interaction: discord.Interaction, prefix: str) -> "InterMessage":
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
        self.guild = interaction.guild

        if not interaction.guild_id:
            channel = self.channel = discord.DMChannel.__new__(discord.DMChannel)
            channel._state = interaction._state
            channel.recipient = interaction.user
            channel.me = interaction.client.user
            channel.id = interaction.channel_id
        else:
            self.channel = copy(interaction.channel)

        self.channel.__class__ = type(
            InterChannel.__name__, (InterChannel, self.channel.__class__), {"__slots__": ()}
        )
        self.recreate_from_interaction(interaction, prefix)

        return self

    def recreate_from_interaction(self, interaction: discord.Interaction, prefix: str):
        assert interaction.data and interaction.client.user

        self.content = f"{prefix}{interaction.namespace.command}"
        if interaction.namespace.arguments:
            self.content = f"{self.content} {interaction.namespace.arguments}"
        if interaction.namespace.attachment:
            self.attachments = [interaction.namespace.attachment]
        else:
            self.attachments = []

        state = self._state
        if interaction.guild_id:
            guild = interaction.guild or discord.Object(interaction.guild_id)
        else:
            guild = None
        self.mentions = []
        resolved = interaction.data.get("resolved", {})
        members = resolved.get("members", {})
        for user_id, user_data in resolved.get("users", {}).items():
            try:
                member_data = members[user_id]
            except KeyError:
                if not guild:
                    uid = int(user_id)
                    if uid == interaction.user.id:
                        self.mentions.append(interaction.user)
                    elif uid == interaction.client.user.id:
                        self.mentions.append(interaction.client.user)  # type: ignore
            else:
                member_data["user"] = user_data
                self.mentions.append(
                    discord.Member(
                        data=member_data,
                        guild=guild,  # type: ignore
                        state=state,
                    )
                )

    def to_reference(self, *, fail_if_not_exists: bool = True):
        return None

    def to_message_reference_dict(self):
        return discord.utils.MISSING

    async def reply(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)

    async def edit(self, *args, **kwargs):
        return self
