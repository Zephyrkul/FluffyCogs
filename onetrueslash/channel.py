from typing import TYPE_CHECKING, Union, cast

import discord

from .utils import Thinking, contexts

if TYPE_CHECKING:
    from discord.context_managers import Typing

    Base = discord.abc.Messageable
else:
    Base = object


class InterChannel(Base):
    __slots__ = ()

    def permissions_for(
        self, obj: Union[discord.abc.User, discord.Role], /
    ) -> discord.Permissions:
        try:
            ctx = contexts.get()
        except LookupError:
            pass
        else:
            interaction = ctx._interaction
            bot_user = cast(discord.ClientUser, ctx.bot.user)
            if obj.id == interaction.user.id:
                return ctx.permissions
            elif obj.id == bot_user.id:
                return ctx.bot_permissions
        return super().permissions_for(obj)  # type: ignore

    def send(self, *args, **kwargs):
        return contexts.get(super()).send(*args, **kwargs)

    def typing(self) -> Union[Thinking, "Typing"]:
        try:
            ctx = contexts.get()
        except LookupError:
            return super().typing()
        else:
            return Thinking(ctx)
