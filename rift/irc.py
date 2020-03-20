import inspect
import keyword
import pydle
import logging
from dataclasses import dataclass, field
from functools import wraps
from typing import Optional


log = logging.getLogger("red.fluffy.rift.irc")
to_log = dict(
    on_connect=(logging.INFO, "%(self)s | Connected."),
    on_join=(logging.DEBUG, "%(self)s | User %(user)s joined channel %(channel)s."),
    on_disconnect=(logging.INFO, "%(self)s | Disconnected (expected? %(expected)s)."),
    on_part=(logging.DEBUG, "%(self)s | User %(user)s left channel %(channel)s: %(message)s"),
)


def _forwarded_event(func):
    event_name = f"pydle_{func.__name__[3:]}"

    @wraps(func)
    async def wrapper(*args, **kwargs):
        # raise TypeError early
        coro = func(*args, **kwargs)
        instance = args[0]
        instance.bot.dispatch(event_name, *args, **kwargs)
        maybe_log = to_log.get(func.__name__)
        if maybe_log and log.isEnabledFor(maybe_log[0]):
            kw = inspect.signature(func).bind(*args, **kwargs)
            kw.apply_defaults()
            log.log(*maybe_log, kw.arguments)
        return await coro

    return wrapper


def _forward_events(cls):
    for attr in dir(cls):
        if attr.startswith("on_"):
            setattr(cls, attr, _forwarded_event(getattr(cls, attr)))
    return cls


@_forward_events
class RiftIRCClient(pydle.Client):
    def __init__(self, bot, **kwargs):
        self.bot = bot
        if "nickname" not in kwargs:
            if bot.user.name.lower().endswith("bot"):
                name = bot.user.name
            else:
                name = f"{bot.user.name}Bot"
            kwargs["nickname"] = name
        kwargs["eventloop"] = bot.loop
        super().__init__(**kwargs)
        # let's not respond to ourselves
        # yes, True is the value to signal that
        # i don't know why
        self._capabilities["echo-message"] = True

    def __getitem__(self, key: str) -> "IRCMessageable":
        normalize = getattr(self, "normalize", None)
        if not normalize:
            log.warning("%s.normalize() not found. Using str instead.", type(self).__qualname__)
            normalize = str
        return IRCMessageable(client=self, name=normalize(key))

    def __str__(self) -> str:
        if self.connected:
            return f"{self.nickname}@{self.network}"
        return f"{self.realname}@<disconnected>"

    def __repr__(self) -> str:
        if self.connected:
            nn = f"nickname={self.nickname!r} network={self.network!r}"
        else:
            nn = f"realname={self.realname!r} network=<disconnected>"
        return f"<{self.__class__.__qualname__} {nn}; {len(self.channels)} connected channels, {len(self.users)} connected users>"


@dataclass(frozen=True)
class IRCMessageable:
    __slots__ = ("client", "name")
    client: RiftIRCClient
    name: str

    @property
    def data(self) -> dict:
        if self.is_channel():
            return self.client.channels.get(self.name, {})
        else:
            return self.client.users.get(self.name, {})

    def is_channel(self) -> bool:
        return self.client.is_channel(self.name)

    def in_channel(self) -> bool:
        return self.client.in_channel(self.name)

    @property
    def nickname(self) -> str:
        if self.is_channel():
            return self.name
        return self.data.get("nickname", self.name)

    def __getattr__(self, attr: str):
        try:
            return getattr(self.data, attr)
        except AttributeError as ae:
            try:
                return self.data[attr]
            except Exception:
                raise AttributeError

    def __getitem__(self, key: str):
        return self.data[key]

    def __len__(self):
        return len(self.data)

    def __str__(self) -> str:
        network = self.client.network if self.client.connected else "<disconnected>"
        if self.is_channel():
            return f"{network}{self.name}"
        name = self.data.get("nickname", self.name)
        return f"{name}@{network}"

    def send(self, content: str):
        return self.client.message(self.name, content)

    async def join(self):
        if self.in_channel():
            return
        if not self.is_channel():
            raise TypeError(f"Messageable {self.name!r} is not a channel.")
        await self.client.join(self.name)
        return self


@dataclass()
class IRCMessage:
    client: RiftIRCClient
    content: str
    author: IRCMessageable
    channel: IRCMessageable
