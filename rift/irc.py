import keyword
import pydle
from dataclass import dataclass, field


def _forward_events(cls):
    for attr in dir(cls):
        if not attr.startswith("on_"):
            continue
        _ = getattr(cls, attr, None)

        async def _event(instance, *args, **kwargs):
            instance.bot.dispatch(f"on_pydle_{attr[3:]}", instance, *args, **kwargs)
            await _(*args, **kwargs)

        setattr(cls, attr, _event)


@_forward_events
class RiftClient(pydle.Client):
    def __init__(self, bot, *args, **kwargs):
        self.bot = bot
        kwargs["eventloop"] = bot.loop
        super().__init__(*args, **kwargs)

    def __getitem__(self, key):
        if not self.in_channel(key):
            raise KeyError(key)
        return IRCChannel(client=self, name=key)


@dataclass(frozen=True)
class IRCChannel:
    client: RiftClient
    name: str
    data: dict = field(init=False, compare=False)

    def __post_init__(self):
        self.data = self.client.channels[self.name]

    def __getattr__(self, attr):
        return self.data[attr]

    def __dir__(self):
        return super().__dir__() + list(
            k for k in self.data.keys() if k.isidentifier() and not keyword.iskeyword(k)
        )

    async def send(self, content: str):
        await self.client.message(self.name, content)
