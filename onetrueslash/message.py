import asyncio
from copy import copy
from itertools import chain
from typing import TypeVar

import discord

from .channel import InterChannel

_TT = TypeVar("_TT", bound=type)


def __step(*args, **kwargs):
    # ensure the coro still yields to the event loop
    return asyncio.sleep(0)


def neuter_coros(cls: _TT) -> _TT:
    for name in dict.fromkeys(chain.from_iterable(dir(clz) for clz in cls.__bases__)):
        if (attr := getattr(cls, name, None)) is None:
            continue
        if asyncio.iscoroutinefunction(attr):
            setattr(cls, name, property(lambda self: __step))
    return cls


@neuter_coros
class InterMessage(discord.Message):
    @classmethod
    async def from_interaction(cls, interaction: discord.Interaction) -> "InterMessage":
        assert interaction.data
        self = InterMessage.__new__(InterMessage)

        self._state = interaction._state
        self._edited_timestamp = None

        self.tts = False
        self.webhook_id = None
        self.mention_everyone = False
        self.embeds = []
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
            self.channel = copy(await interaction.user.create_dm())
        else:
            self.channel = copy(interaction.channel)

        self.channel.__class__ = type(
            InterChannel.__name__, (InterChannel, self.channel.__class__), {"__slots__": ()}
        )
        self.recreate_from_interaction(interaction)

        return self

    def recreate_from_interaction(self, interaction: discord.Interaction):
        assert interaction.data

        self.content = f"/{interaction.data['name']} {interaction.namespace.command} {interaction.namespace.arguments}"
        if "attachment" in interaction.namespace:
            self.attachments = [interaction.namespace.attachment]
        else:
            self.attachments = []

        self.mentions = []
        self.role_mentions = []
        if resolved := interaction.data.get("resolved"):
            if interaction.guild_id:
                guild = interaction.guild
                if "members" in resolved:
                    for id, data in resolved["members"].items():
                        id = int(id)
                        if id == interaction.user.id:
                            self.mentions.append(interaction.user)
                        elif guild and (member := guild.get_member(id)):
                            self.mentions.append(member)
                        # TODO: edge cases
                if "roles" in resolved:
                    for id, data in resolved["roles"].items():
                        id = int(id)
                        if guild and (role := guild.get_role(id)):
                            self.role_mentions.append(role)
                        # TODO: edge cases
            else:
                if "users" in resolved:
                    for id, data in resolved["users"].items():
                        id = int(id)
                        if id == interaction.user.id:
                            self.mentions.append(interaction.user)
                        elif user := interaction.client.get_user(id):
                            self.mentions.append(user)

    def to_reference(self, *, fail_if_not_exists: bool = True):
        return None

    def to_message_reference_dict(self):
        return discord.utils.MISSING

    async def reply(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)
