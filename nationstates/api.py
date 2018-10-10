import aiohttp
import asyncio
import collections
import re
import sys
from bisect import insort
from lxml import etree
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional, Sequence, TYPE_CHECKING
from urllib.parse import urlencode, urlunparse

from redbot.core import __version__ as redbot_version

import traceback


API_URL = ("https", "www.nationstates.net", "/cgi-bin/api.cgi")
RATE = collections.namedtuple("RateLimit", ("requests", "per", "pad", "retry"))(50, 30, 2, 900)


_NS = SimpleNamespace()
_versions = (
    "Python/" + ".".join(map(str, sys.version_info[:2])),
    "aiohttp/" + aiohttp.__version__,
    "redbot/" + redbot_version,
)
_nvalid = re.compile(r"[^-\w]+")


def start(loop: asyncio.AbstractEventLoop) -> None:
    _NS.loop = loop
    _NS.semaphore = _NSSemaphore(loop=loop)
    _NS.session = aiohttp.ClientSession(
        loop=loop, raise_for_status=True, response_class=_NSResponse
    )


def close() -> asyncio.Task:
    t = _NS.loop.create_task(_NS.session.close())
    for attr in dir(_NS):
        if not attr.startswith("_"):
            delattr(_NS, attr)
    return t


def agent(new_agent: str = None) -> str:
    if new_agent:
        _NS.agent = " ".join((str(new_agent), *_versions))
    return _NS.agent


class _NSSemaphore(asyncio.BoundedSemaphore):
    def __init__(self, *, loop=None):
        super().__init__(RATE.requests - RATE.pad, loop=loop)

    def tmr(self, xra: int):
        self._value = self._bound_value - RATE.requests
        return self.release(RATE.requests, time=xra)

    def sync(self, xrlrs: int):
        if xrlrs <= 0:
            return
        diff = (self._bound_value - xrlrs) - self._value
        if diff > 0:
            return self.release(diff, time=0)
        elif diff < 0:
            self._value += diff
            return self.release(-diff)

    def locked(self):
        return self._value <= 0

    def release(self, amount: int = 1, *, time: float = RATE.per):
        super_release = super().release

        def delayed():
            for _ in range(amount):
                super_release()

        if time > 0:
            return self._loop.call_later(time, delayed)
        return delayed()


class _NSResponse(aiohttp.ClientResponse):
    async def start(self, conn):
        response = None
        try:
            async with _NS.semaphore:
                response = await super().start(conn)
                return response
        except aiohttp.ClientResponseError as e:
            response = e
            raise
        finally:
            if response:
                if response.status == 429:
                    xra = response.headers.get("X-Retry-After", RATE.retry)
                    _NS.semaphore.tmr(int(xra))
                else:
                    xrlrs = response.headers.get("X-ratelimit-requests-seen", 0)
                    _NS.semaphore.sync(int(xrlrs))


class _NSElement(etree.ElementBase):
    def __getitem__(self, item):
        if isinstance(item, str):
            e = self.find(item)
            if e is None or e.attrib or len(e):
                return e
            return e.text
        return super().__getitem__(item)

    def __setitem__(self, item, value):
        if isinstance(item, str):
            e = self.find(item)
            if isinstance(value, collections.abc.Mapping):
                e.attrib = value
            else:
                e.text = str(value)
        else:
            super().__setitem__(item, value)


_parser = etree.XMLParser(remove_blank_text=True)
_parser.set_element_class_lookup(etree.ElementDefaultClassLookup(element=_NSElement))


class _ApiMeta(type):
    def __getattr__(cls, name: str) -> "_Api":
        instance = cls()
        return getattr(instance, name)


class _Api(metaclass=_ApiMeta):
    def __init__(self, value: str = None, **kwargs: str):
        self._q = set()
        self._kw = {}
        self._request = None
        if not value:
            self.value = None
        self(value, **kwargs)

    def __await__(self):
        if not self:
            raise RuntimeError(f"Bad request: {self}")
        url = str(self)

        async def actual():
            async with _NS.session.request(
                "GET", url, headers={"User-Agent": _NS.agent}
            ) as response:
                text = await response.text()
            root = etree.fromstring(text, _parser, base_url=url)
            root.insert(0, _parser.makeelement("HEADERS", attrib=response.headers))
            return root

        return (yield from actual().__await__())

    @property
    def key(self) -> str:
        return type(self).__name__.lower()

    @property
    def value(self) -> Optional[str]:
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        self._value = _nvalid.sub("", "_".join(str(value).lower().split())) if value else None

    def __bool__(self):
        return bool(self.value)

    def __call__(self, value: Optional[str] = None, **kwargs: str) -> "_Api":
        if value:
            self.value = value
        if kwargs:
            if "q" in kwargs:
                self.getattr(*kwargs.pop("q").split())
            key = self.key
            if key and key in kwargs:
                self.value = kwargs.pop(key)
            self._kw.update(
                (k.lower(), " ".join(sorted(str(v).lower().split())) if v else None)
                for k, v in kwargs.items()
            )
            self._kw = {k: v for k, v in self._kw.items() if v}
        return self

    def __eq__(self, other: Any) -> bool:
        return str(self) == other

    def __getattr__(self, *names: str) -> "_Api":
        self._q.update(" ".join(names).split())
        return self

    getattr = __getattr__

    def __repr__(self) -> str:
        argument = repr(self.value) if self.key else None
        kwargs = ", ".join(f"{k}={v!r}" for k, v in sorted(self._kw.items()))
        args = ", ".join(filter(bool, (argument, kwargs)))
        attrs = ".".join(sorted(self._q))
        name = type(self).__name__
        if args:
            name += f"({args})"
        if attrs:
            name += f".{attrs}"
        return name

    def __str__(self) -> str:
        key, value = self.key, self.value
        params = [(key, value)] if all((key, value)) else []
        params.append(("q", " ".join(sorted(self._q))))
        params.extend(sorted(self._kw.items()))
        return urlunparse((*API_URL, None, urlencode(params), None))


class Nation(_Api):
    pass


class Region(_Api):
    pass


class World(_Api):
    @property
    def key(self) -> None:
        return None

    # pylint: disable=E1101
    @_Api.value.setter
    def value(self, value: None) -> None:
        if value is not None:
            raise TypeError(value)

    def __bool__(self):
        return bool(self._q)


class WA(_Api):
    # pylint: disable=E1101
    @_Api.value.setter
    def value(self, value: str) -> None:
        if not value:
            self._value = None
            return
        value = str(value).lower()
        if value in ("ga", "general", "assembly", "general assembly", "1"):
            self._value = "1"
        elif value in ("sc", "security", "council", "security council", "2"):
            self._value = "2"
        else:
            raise ValueError(value)
