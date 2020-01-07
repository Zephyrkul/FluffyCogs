import keyword
import pydle
import logging
from dataclasses import dataclass, field
from functools import wraps


log = logging.getLogger("red.fluffy.rift.irc")


def _forwarded_event(func):
    event_name = f"pydle_{func.__name__[3:]}"

    @wraps(func)
    async def wrapper(*args, **kwargs):
        instance = args[0]
        instance.bot.dispatch(event_name, *args, **kwargs)
        return await func(*args, **kwargs)

    return wrapper


def _forward_events(cls):
    for attr in dir(cls):
        if attr.startswith("on_"):
            setattr(cls, attr, _forwarded_event(getattr(cls, attr)))
    return cls


@_forward_events
class RiftIRCClient(pydle.Client):
    def __init__(self, bot, *args, **kwargs):
        self.bot = bot
        kwargs["eventloop"] = bot.loop
        super().__init__(*args, **kwargs)

    def __getitem__(self, key):
        return IRCMessageable(client=self, name=self.normalize(key))


@dataclass(frozen=True)
class IRCMessageable:
    client: RiftIRCClient
    name: str
    _: dict = field(init=False, compare=False)

    def __post_init__(self):
        if self.name.startswith("#"):
            object.__setattr__(self, "_", self.client.channels[self.name])
        else:
            object.__setattr__(self, "_", self.client.users[self.name])

    def __getattr__(self, attr):
        return self._[attr]

    __getitem__ = __getattr__

    def __dir__(self):
        return super().__dir__() + list(
            k for k in self._.keys() if k.isidentifier() and not keyword.iskeyword(k)
        )

    async def send(self, content: str):
        await self.client.message(self.name, content)
