import contextlib
import discord
import weakref
from typing import List, Optional, Set, Union
from dataclasses import dataclass, field

from .irc import IRCMessageable


Destination = Union[discord.abc.Messageable, IRCMessageable]
User = Union[discord.User, IRCMessageable]
_links = weakref.WeakValueDictionary({})


@dataclass
class Link:
    channel: Destination
    _whitelist: Set[User] = field(init=False, default_factory=set, compare=False)
    _blacklist: Set[User] = field(init=False, default_factory=set, compare=False)
    _oneway: bool = field(init=False, default=False)

    def __new__(cls, channel, *args, **kwargs):
        if isinstance(channel, cls):
            return channel
        with contextlib.suppress(KeyError):
            return _links[channel]
        self = super().__new__(cls)
        _links[channel] = self
        return self

    def is_allowed(self, user: User):
        if self.whitelist:
            return user in self.whitelist
        if self.blacklist:
            return user not in self.blacklist
        return True

    @staticmethod
    def _combine(one, many):
        if one and many:
            return set((one, *many))
        if one and not many:
            return {one}
        if not one and many:
            return set(many)
        return set()

    def whitelist(
        self,
        *,
        add: User = None,
        add_all: List[User] = None,
        remove: User = None,
        remove_all: List[User] = None
    ):
        add, remove = self._combine(add, add_all), self._combine(remove, remove_all)
        if add == remove:
            return
        if add:
            self.whitelist.update(add)
        if remove:
            self.whitelist.difference_update(remove)

    def blacklist(
        self,
        *,
        add: User = None,
        add_all: List[User] = None,
        remove: User = None,
        remove_all: List[User] = None
    ):
        add, remove = self._combine(add, add_all), self._combine(remove, remove_all)
        if add == remove:
            return
        if add:
            self.blacklist.update(add)
        if remove:
            self.blacklist.difference_update(remove)


@dataclass
class Nexus:
    links: Set[Link] = field(default_factory=set)

    __hash__ = None

    def can_send(self, from_link, from_user: User):
        from_link = Link(from_link)
        return from_link in self.links and from_link.is_allowed(from_user)

    async def forward(self, from_link, from_user: User, *args, **kwargs):
        sent = []
        from_link = Link(from_link)
        if not from_link.is_allowed(from_user):
            return sent
        for to_link in self.links:
            if to_link == from_link:
                continue
            if not to_link.is_allowed(from_user):
                continue
            sent.append(await to_link.channel.send(*args, **kwargs))
        return sent


@dataclass
class SourcedNexus(Nexus):
    source: Link
