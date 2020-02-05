import inspect
import keyword
import pydle
import logging
from dataclasses import dataclass, field
from functools import wraps


log = logging.getLogger("red.fluffy.rift.irc")
to_log = dict(
    # first %s is always client
    on_connect=(logging.INFO, "%(self)s | Connected."),
    on_join=(logging.DEBUG, "%(self)s | User %(user)s joined channel %(channel)s."),
    on_disconnect=(logging.INFO, "Client disconnected (expected? %(expected)s)."),
    on_part=(logging.DEBUG, "%(self)s | User %(user)s left channel %(channel)s: %(message)s"),
)


def _forwarded_event(func):
    event_name = f"pydle_{func.__name__[3:]}"

    @wraps(func)
    async def wrapper(*args, **kwargs):
        coro = func(*args, **kwargs)
        maybe_log = to_log.get(func.__name__)
        if maybe_log and log.isEnabledFor(maybe_log[0]):
            kw = inspect.signature(func).bind(*args, **kwargs)
            kw.apply_defaults()
            log.log(*maybe_log, kw.arguments)
        instance = args[0]
        instance.bot.dispatch(event_name, *args, **kwargs)
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
        kwargs.setdefault("nickname", bot.user.name)
        kwargs["eventloop"] = bot.loop
        super().__init__(**kwargs)
        # let's not respond to ourselves
        # yes, True is the value to signal that
        # i don't know why
        self._capabilities["echo-message"] = True

    def __getitem__(self, key: str) -> "IRCMessageable":
        normalize = getattr(self, "normalize", None)
        if not normalize:
            log.warning("%s.normalize() not found. Using str instead.", type(self).__name__)
            normalize = str
        return IRCMessageable(client=self, name=normalize(key))

    def __str__(self) -> str:
        if self.connected:
            return f"{self.nickname}@{self.network}"
        return super()


@dataclass(frozen=True)
class IRCMessageable:
    client: RiftIRCClient
    name: str
    data: dict = field(init=False, compare=False)

    def __post_init__(self):
        if self.name.startswith("#"):
            object.__setattr__(self, "data", self.client.channels[self.name])
        else:
            object.__setattr__(self, "data", self.client.users[self.name])

    def __getattr__(self, attr: str):
        try:
            return getattr(self.data, attr)
        except AttributeError as ke:
            try:
                return self.data[attr]
            except Exception:
                raise ke

    def __getitem__(self, key: str):
        return self.data[key]

    def __dir__(self):
        return super().__dir__() + [
            k for k in self.data.keys() if k.isidentifier() and not keyword.iskeyword(k)
        ]

    def __str__(self):
        return self.name

    def send(self, content: str):
        return self.client.message(self.name, content)
