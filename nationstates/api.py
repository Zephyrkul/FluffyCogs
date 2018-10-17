import aiohttp
import asyncio
import collections
import contextlib
import re
import sys
import zlib
from collections.abc import Iterable, Mapping
from io import BytesIO
from lxml import etree
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, urlunparse


API_URL = ("https", "www.nationstates.net", "/cgi-bin/api.cgi")
RATE = collections.namedtuple("RateLimit", ("requests", "per", "pad", "retry"))(50, 30, 2, 900)


_NS = SimpleNamespace()
_versions = (
    "Python/" + ".".join(map(str, sys.version_info[:2])),
    "aiohttp/" + aiohttp.__version__,
)
_nvalid = re.compile(r"[-\w]+")
_nsplit = lambda s: (m.group(0) for m in _nvalid.finditer(s) if m.group(0).strip())


def start(loop: asyncio.AbstractEventLoop = None) -> None:
    if any(a in dir(_NS) for a in ("loop", "semaphore", "session")):
        raise RuntimeError("API session is already started")
    loop = loop or asyncio.get_event_loop()
    _NS.loop = loop
    _NS.semaphore = _NSSemaphore(loop=loop)
    _NS.session = aiohttp.ClientSession(
        loop=loop, raise_for_status=True, response_class=_NSResponse
    )


def close() -> asyncio.Task:
    t = _NS.loop.create_task(_NS.session.close())
    for attr in dir(_NS):
        if not attr.startswith("_") and attr != "agent":
            delattr(_NS, attr)
    return t


def agent(new_agent: str = None) -> str:
    if new_agent:
        _NS.agent = " ".join((str(new_agent), *_versions))
    return _NS.agent


def wait_if(coro, *, timeout: float, wait_for=False):
    sem = _NS.semaphore
    loop = sem._loop
    if sem.locked():
        to_wait = sem.next_available[0] - (loop.time() + timeout)
        if to_wait > 0:
            raise asyncio.TimeoutError(to_wait)
    if wait_for:
        return asyncio.wait_for(coro, timeout=timeout)
    return coro


class _NSSemaphore(asyncio.BoundedSemaphore):
    def __init__(self, *, loop=None):
        super().__init__(RATE.requests - RATE.pad, loop=loop)
        self.next_available = collections.deque(maxlen=self._bound_value)

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
        if amount <= 0:
            return

        super_release = super().release

        def delayed():
            for _ in range(amount):
                self.next_available.popleft()
                super_release()

        if time > 0:
            handle = self._loop.call_later(time, delayed)
            self.next_available.extend([handle.when()] * amount)
            return handle
        return delayed()


class _NSResponse(aiohttp.ClientResponse):
    __slots__ = ()

    async def start(self, conn):
        if urlparse(str(self.real_url))[: len(API_URL)] != API_URL:
            # don't use the semaphore
            return await super().start(conn)
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
                    xra = response.headers["X-Retry-After"]
                    _NS.semaphore.tmr(int(xra))
                else:
                    xrlrs = response.headers.get("X-ratelimit-requests-seen", 0)
                    _NS.semaphore.sync(int(xrlrs))


class _NSElement(etree.ElementBase):
    __slots__ = ()

    def __getitem__(self, item):
        with contextlib.suppress(TypeError):
            return super().__getitem__(item)
        e = self.find(item)
        if e is None:
            raise KeyError(item)
        if e.attrib or len(e):
            return e
        return e.text

    def __setitem__(self, item, value):
        with contextlib.suppress(TypeError):
            return super().__setitem__(item, value)
        e = self.find(item)
        if e is None:
            raise KeyError(item)
        if isinstance(value, collections.abc.Mapping):
            e.attrib = value
        else:
            e.text = str(value)


class _NSXmlIter:
    __slots__ = "url", "__clear"

    def __init__(self, api: "_Api"):
        if not api:
            raise ValueError("Bad request")
        self.url = str(api)
        self.__clear = True


class _ApiMeta(type):
    def __getattr__(cls, name: str) -> "_Api":
        instance = cls()
        return getattr(instance, name)


class _DumpsMeta(_ApiMeta):
    @property
    def dumps(cls):
        url = urlunparse(
            (*API_URL[:2], f"/pages/{cls.__name__.lower()}s.xml.gz", None, None, None)
        )
        return cls.__aiter__(None, url=url)


class _Api(metaclass=_ApiMeta):
    __slots__ = "_q", "_kw", "_value"

    def __init__(self, value: Optional[str] = None, **kwargs: str):
        self._q = set()
        self._kw = {}
        if not value:
            self.value = None
        self(value, **kwargs)

    async def __await(self):
        async for element in self.__aiter__(clear=False):
            pass
        return element

    def __await__(self):
        return self.__await().__await__()

    async def __aiter__(self, *, url: str = None, clear: bool = True):
        if not self and not url:
            raise ValueError("Bad request")
        url = url or str(self)

        parser = etree.XMLPullParser(["end"], base_url=url, remove_blank_text=True)
        parser.set_element_class_lookup(etree.ElementDefaultClassLookup(element=_NSElement))
        events = parser.read_events()

        async with _NS.session.request("GET", url, headers={"User-Agent": _NS.agent}) as response:
            yield parser.makeelement("HEADERS", attrib=response.headers)
            if "application/x-gzip" in response.headers["Content-Type"]:
                dobj = zlib.decompressobj(16 + zlib.MAX_WBITS)
            else:
                dobj = None
            async for data, _ in response.content.iter_chunks():
                if dobj:
                    data = dobj.decompress(data)
                parser.feed(data)
                for _, element in events:
                    yield element
                    if clear:
                        element.clear()

    def __bool__(self):
        return bool(self.value)

    def __call__(self, value: Optional[str] = None, **kwargs: str) -> "_Api":
        if value:
            self.value = value
        if kwargs:
            if "q" in kwargs:
                self._q.update(_nsplit(str(kwargs.pop("q")).lower()))
            key = self.key
            if key and key in kwargs:
                self.value = kwargs.pop(key)
            for k, v in kwargs.items():
                if not v:
                    continue
                v = " ".join(_nsplit(str(v).lower()))
                if v:
                    self._kw[k] = v
        return self

    def __iadd__(self, other):
        if isinstance(other, type(self)):
            self._q |= other._q
            self._kw.update(other._kw)
            return self
        if isinstance(other, str):
            return self(other)
        if isinstance(other, Iterable):
            self._q.update(_nsplit(" ".join(other).lower()))
            return self
        if isinstance(other, Mapping):
            return self(**other)
        return NotImplemented

    __add__ = __iadd__

    def __getattr__(self, name: str) -> "_Api":
        if name.startswith("_"):
            raise AttributeError
        self._q.update(_nsplit(name.lower()))
        return self

    def __repr__(self) -> str:
        argument = repr(self.value) if self.key else None
        kwargs = ", ".join(f"{k}={v!r}" for k, v in self._kw.items())
        args = ", ".join(filter(bool, (argument, kwargs)))
        attrs = ".".join(self._q)
        name = type(self).__name__
        if args:
            name += f"({args})"
        if attrs:
            name += f".{attrs}"
        return name

    def __str__(self) -> str:
        main = self.key, self.value
        params = [main] if all(main) else []
        if self._q:
            params.append(("q", " ".join(self._q)))
        if self._kw:
            params.extend(self._kw.items())
        return urlunparse((*API_URL, None, urlencode(params), None))

    @property
    def key(self) -> str:
        return type(self).__name__.lower()

    @property
    def value(self) -> Optional[str]:
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        self._value = "_".join(_nsplit(str(value).lower())) if value else None


class Nation(_Api, metaclass=_DumpsMeta):
    __slots__ = ()


class Region(_Api, metaclass=_DumpsMeta):
    __slots__ = ()


class World(_Api):
    __slots__ = ()

    @property
    def key(self) -> None:
        return None

    # pylint: disable=E1101
    @_Api.value.setter
    def value(self, value: None) -> None:
        if value is not None:
            raise TypeError(value)
        _Api.value.fset(value)

    def __bool__(self):
        return bool(self._q)


class WA(_Api):
    __slots__ = ()

    # pylint: disable=E1101
    @_Api.value.setter
    def value(self, value: str) -> None:
        if not value:
            _Api.value.fset(self, None)
            return
        value = str(value).lower()
        if value in ("wa", "ga", "1"):
            _Api.value.fset(self, "1")
        elif value in ("sc", "2"):
            _Api.value.fset(self, "2")
        else:
            raise ValueError(value)
