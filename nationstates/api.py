import aiohttp
import asyncio
import collections
import contextlib
import re
import sys
import zlib
from abc import ABCMeta
from collections.abc import Iterable, Mapping, MutableMapping
from enum import Enum
from io import BytesIO
from itertools import repeat
from lxml import etree
from types import MappingProxyType, SimpleNamespace
from typing import (
    Any as _Any,
    Optional as _Optional,
    Mapping as _Mapping,
    Sequence as _Sequence,
    Tuple as _Tuple,
    Union as _Union,
)
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


API_URL = ("https", "www.nationstates.net", "/cgi-bin/api.cgi")
LINK_RE = re.compile(
    r"\b(?:(?:https?:\/\/)?(?:www\.)?nationstates\.net\/(?:(nation|region)=)?)?([-\w\s]+)\b", re.I
)
RATE = collections.namedtuple("RateLimit", ("requests", "per", "pad", "retry"))(50, 30, 2, 900)


_versions = (
    "Python/" + ".".join(map(str, sys.version_info[:2])),
    "aiohttp/" + aiohttp.__version__,
)
_nvalid = re.compile(r"[a-zA-Z\d-]+")
_nsplit = lambda s: (m.group(0) for m in _nvalid.finditer(s))


def link_extract(link: str) -> _Optional[_Tuple[str, str]]:
    match = LINK_RE.match(link)
    if not match:
        return None
    match = match.group(1, 2)
    if match[0]:
        return match
    return ("nation", *match[1:])


def wait_if(coro, timeout: float, *, wait_for=False):
    sem = Api.semaphore
    loop = sem._loop
    if sem.locked():
        to_wait = sem.next_available[0] - (loop.time() + timeout)
        if to_wait > 0:
            raise asyncio.TimeoutError(to_wait)
    if wait_for:
        return asyncio.wait_for(coro, timeout=timeout)
    return coro


class _fset(frozenset):
    def __str__(self):
        return super().__str__()[len(type(self).__name__) + 1 : -1]


class _Specials:
    @staticmethod
    def _default(v):
        return set(_nsplit(v.lower()))

    @staticmethod
    def a(v):
        return v.lower()

    @staticmethod
    def nation(v):
        return "_".join(_nsplit(v.lower()))

    region = nation

    @classmethod
    def view(cls, v):
        v = v.lower().split(".")
        v[1] = ",".join(sorted(map(cls.nation, v[1].split(","))))
        return ".".join(map(str.strip, v))

    dispatchauthor = nation

    @staticmethod
    def dispatchcategory(v):
        v = v.split(":")
        return ":".join(filter(bool, (s.strip().title() for s in v)))


def _normalize_dicts(*dicts: _Mapping[str, str]):
    final = {}
    for d in dicts:
        for k, v in d.items():
            if not k or not v:
                continue
            k = "".join(_nsplit(str(k).lower()))
            v = getattr(_Specials, k, _Specials._default)(str(v))
            if k and v:
                if isinstance(v, str):
                    final[k] = v
                    continue
                fv = final.setdefault(k, type(v)())
                if hasattr(fv, "extend"):
                    fv.extend(v)
                else:
                    fv.update(v)
    convert = {list: tuple, set: _fset, str: str}
    return {k: convert[type(v)](v) for k, v in final.items()}


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
                with contextlib.suppress(IndexError):
                    self.next_available.popleft()
                super_release()

        if time > 0:
            when = self._loop.time() + time
            handle = self._loop.call_later(time, delayed)
            self.next_available.extend(repeat(when, amount))
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
            # pylint: disable=E1701
            async with Api.semaphore:
                response = await super().start(conn)
                return response
        except aiohttp.ClientResponseError as e:
            response = e
            raise
        finally:
            if response:
                if response.status == 429:
                    xra = response.headers["X-Retry-After"]
                    Api.semaphore.tmr(int(xra))
                else:
                    xrlrs = response.headers.get("X-ratelimit-requests-seen", 0)
                    Api.semaphore.sync(int(xrlrs))


class _NSElement(etree.ElementBase, MutableMapping):
    __slots__ = ()

    def __delitem__(self, key):
        element = self[key]
        element.getparent().remove(element)

    def __getitem__(self, key):
        with contextlib.suppress(TypeError):
            return super().__getitem__(key)
        e = self.find(key)
        if e is None:
            raise KeyError(key)
        if e.attrib or len(e):
            return e
        return e.text

    def __iter__(self):
        return self.iter()

    # __len__ implemented by ElementBase

    def __setitem__(self, key, value):
        with contextlib.suppress(TypeError):
            return super().__setitem__(key, value)
        e = self.find(key)
        if e is None:
            raise KeyError(key)
        if isinstance(value, collections.abc.Mapping):
            e.attrib = value
        else:
            e.text = str(value)


class Dumps(Enum):
    __slots__ = ()

    NATIONS = urlunparse((*API_URL[:2], "/pages/nations.xml.gz", None, None, None))
    NATION = NATIONS
    REGIONS = urlunparse((*API_URL[:2], "/pages/regions.xml.gz", None, None, None))
    REGION = REGIONS

    async def __aiter__(self):
        url = self.value

        parser = etree.XMLPullParser(["end"], base_url=url, remove_blank_text=True)
        parser.set_element_class_lookup(etree.ElementDefaultClassLookup(element=_NSElement))
        events = parser.read_events()
        dobj = zlib.decompressobj(16 + zlib.MAX_WBITS)

        async with Api.session.request("GET", url, headers={"User-Agent": Api.agent}) as response:
            yield parser.makeelement("HEADERS", attrib=response.headers)
            async for data, _ in response.content.iter_chunks():
                parser.feed(dobj.decompress(data))
                for _, element in events:
                    yield element
                    element.clear()


class _ApiMeta(type):
    def __init__(cls, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cls._agent, cls._loop, cls._semaphore, cls._session = None, None, None, None

    @property
    def agent(cls):
        return cls._agent

    @agent.setter
    def agent(cls, value):
        if value:
            cls._agent = " ".join((str(value), *_versions))

    def close(cls) -> _Optional[asyncio.Task]:
        t = None
        with contextlib.suppress(TypeError):
            t = cls._loop.create_task(cls._session.close())
        cls._loop, cls._semaphore, cls._session = None, None, None
        return t

    @property
    def closed(cls):
        return bool(cls._agent and cls._loop)

    def start(cls, loop: asyncio.AbstractEventLoop = None) -> None:
        if cls._loop:
            raise RuntimeError("API session is already started!")
        loop = loop or asyncio.get_event_loop()
        cls._loop = loop
        cls._semaphore = _NSSemaphore(loop=loop)
        cls._session = aiohttp.ClientSession(
            loop=loop, raise_for_status=True, response_class=_NSResponse
        )

    @property
    def loop(cls):
        return cls._loop

    @property
    def semaphore(cls):
        return cls._semaphore

    @property
    def session(cls):
        return cls._session


class Api(metaclass=_ApiMeta):
    __slots__ = ("__dict",)

    def __init__(self, *shards: _Union[str, _Mapping[str, str]], **kwargs: str):
        dicts = [kwargs] if kwargs else []
        for shard in shards:
            if isinstance(shard, Mapping) and shard:
                dicts.append(shard)
            elif shard:
                dicts.append({"q": shard})
        self.__dict = MappingProxyType(_normalize_dicts(*dicts))

    async def __await(self):
        # pylint: disable=E1133
        async for element in self.__aiter__(clear=False):
            pass
        return element

    def __await__(self):
        return self.__await().__await__()

    async def __aiter__(self, *, clear: bool = True):
        if not self:
            raise ValueError("Bad request")
        url = str(self)

        parser = etree.XMLPullParser(["end"], base_url=url, remove_blank_text=True)
        parser.set_element_class_lookup(etree.ElementDefaultClassLookup(element=_NSElement))
        events = parser.read_events()

        async with type(self).session.request(
            "GET", url, headers={"User-Agent": type(self).agent}
        ) as response:
            yield parser.makeelement("HEADERS", attrib=response.headers)
            encoding = response.headers["Content-Type"].split("charset=")[1].split(",")[0]
            async for data, _ in response.content.iter_chunks():
                parser.feed(data.decode(encoding))
                for _, element in events:
                    yield element
                    if clear:
                        element.clear()

    def __add__(self, other: _Any) -> "Api":
        with contextlib.suppress(Exception):
            return type(self)(self, other)
        return NotImplemented

    def __bool__(self):
        return any(a in self for a in ("a", "nation", "region", "q", "wa"))

    def __contains__(self, key):
        return key in self.__dict

    def __dir__(self):
        return set(super().__dir__()).union(dir(self.__dict))

    def __getattribute__(self, name):
        try:
            return super().__getattribute__(name)
        except AttributeError:
            with contextlib.suppress(AttributeError):
                return getattr(self.__dict, name)
            raise

    def __getitem__(self, key):
        return self.__dict[str(key).lower()]

    def __iter__(self):
        return iter(self.__dict)

    def __len__(self):
        return len(self.__dict)

    def __repr__(self) -> str:
        return "{}({})".format(
            type(self).__name__,
            ", ".join(
                "{}={!r}".format(k, v if isinstance(v, str) else " ".join(v))
                for k, v in self.__dict.items()
            ),
        )

    def __str__(self) -> str:
        params = [(k, v if isinstance(v, str) else " ".join(v)) for k, v in self.items()]
        return urlunparse((*API_URL, None, urlencode(params), None))

    def copy(self):
        return type(self)(**self.__dict)

    @classmethod
    def from_url(cls, url: str, *args, **kwargs):
        parsed_url = urlparse(str(url))
        url = parsed_url[: len(API_URL)]
        if any(url) and url != API_URL:
            raise ValueError("URL must be solely query parameters or an API url")
        return cls(*args, dict(parse_qsl(parsed_url.query)), kwargs)
