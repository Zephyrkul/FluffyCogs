from copy import copy

import discord

from .channel import InterChannel


class InterMessage(discord.Message):
    @classmethod
    def from_interaction(cls, interaction: discord.Interaction) -> "InterMessage":
        assert interaction.data
        self = InterMessage.__new__(InterMessage)

        self._state = interaction._state
        self._edited_timestamp = None

        self.tts = False
        self.channel = copy(interaction.channel)
        self.webhook_id = None
        self.mention_everyone = False
        self.embeds = []
        self.id = interaction.id
        self.author = interaction.user
        self.attachments = []  # TODO: application command attachments
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

        self.channel.__class__ = type(
            InterChannel.__name__, (InterChannel, self.channel.__class__), {"__slots__": ()}
        )
        self.recreate_from_interaction(interaction)

        return self

    def recreate_from_interaction(self, interaction: discord.Interaction):
        assert interaction.data

        self.content = f"/{interaction.data['name']} "
        for option in interaction.data["options"]:
            self.content += f"{option['value']} "
        self.content = self.content.rstrip()

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
        return
