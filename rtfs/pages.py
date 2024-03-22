from __future__ import annotations

import sys
from itertools import islice
from typing import (
    TYPE_CHECKING,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Generic,
    Iterable,
    Sequence,
    SupportsIndex,
    TypeVar,
)
from typing_extensions import Never

import discord
from discord.ui import Button, View, button
from discord.utils import MISSING

_T_co = TypeVar("_T_co", covariant=True)

if TYPE_CHECKING:

    def aiter(__aiter: AsyncIterable[_T_co]) -> AsyncIterator[_T_co]: ...
    def anext(__aiter: AsyncIterator[_T_co]) -> Awaitable[_T_co]: ...
elif sys.version_info < (3, 10):
    from operator import methodcaller

    aiter = methodcaller("__aiter__")
    anext = methodcaller("__anext__")
    del methodcaller


async def _take(iterator: AsyncIterator[_T_co], stop: int) -> AsyncIterator[_T_co]:
    count = 0
    while count < stop:
        try:
            item = await anext(iterator)
        except StopAsyncIteration:
            return
        count += 1
        yield item


class _SequenceSource(Generic[_T_co]):
    __slots__ = ("_cache",)

    def __init__(self, __seq: Sequence[_T_co]) -> None:
        self._cache = __seq

    def __getitem__(self, item: SupportsIndex) -> _T_co:
        return self._cache[item]

    async def _fill_index(self, idx: int) -> int:
        return len(self._cache)


class _IterSource(Generic[_T_co]):
    __slots__ = ("_cache", "_iter")

    def __init__(self, __seq: Iterable[_T_co]) -> None:
        self._iter = iter(__seq)
        self._cache: list[_T_co] = []

    def __getitem__(self, item: SupportsIndex) -> _T_co:
        return self._cache[item]

    async def _fill_index(self, idx: int) -> int:
        if idx < 0:
            it = self._iter
        else:
            it = islice(self._iter, max(0, idx + 1 - len(self._cache)))
        self._cache.extend(it)
        return len(self._cache)


class _AsyncIterSource(Generic[_T_co]):
    __slots__ = ("_cache", "_aiter")

    def __init__(self, __seq: AsyncIterable[_T_co]) -> None:
        self._aiter = aiter(__seq)
        self._cache: list[_T_co] = []

    def __getitem__(self, idx: SupportsIndex) -> _T_co:
        return self._cache[idx]

    async def _fill_index(self, idx: int) -> int:
        if idx < 0:
            it = self._aiter
        else:
            it = _take(self._aiter, max(0, idx + 1 - len(self._cache)))
        async for i in it:
            self._cache.append(i)
        return len(self._cache)


class Pages:
    __slots__ = ("_author_id", "_index", "_message", "_source", "_timeout_content", "_view")

    def __init__(
        self,
        *,
        source: Iterable[str] | AsyncIterable[str],
        author_id: int,
        starting_index: int = 0,
        timeout_content: str | int | None = MISSING,
        timeout: float | None = 180.0,
    ):
        if isinstance(source, Sequence):
            self._source = _SequenceSource(source)
        elif isinstance(source, Iterable):
            self._source = _IterSource(source)
        elif isinstance(source, AsyncIterable):
            self._source = _AsyncIterSource(source)
        else:
            raise TypeError(
                f"Expected Iterable or AsyncIterable, got {source.__class__.__name__!r}"
            )
        self._author_id = author_id
        self._message: discord.Message | None = None
        self._index = starting_index
        self._timeout_content = timeout_content
        self._view = _PageView(parent=self, timeout=timeout)

    async def _set_index(self, value: int, /):
        source_len = await self._source._fill_index(
            # -1 if value is negative, 2 if value is 0, value + 1 if value is positive
            # signum(x) = (x > 0) - (x < 0)
            [-1, 2, value + 1][1 + (value > 0) - (value < 0)]
        )
        self._index = value = value % source_len
        offset = source_len == 2
        view = self._view
        if source_len == 1:
            view.clear_items()
        elif value == 0:
            view._update_button(view.first, disabled=True)
            view._update_button(view.last, disabled=False)
            view._update_inner_buttons(range(0 - offset, 3 - offset))
        elif value == source_len - 1:
            view._update_button(view.first, disabled=False)
            view._update_button(view.last, disabled=True)
            view._update_inner_buttons(range(value - 2 + offset, value + 1 + offset))
        else:
            view._update_button(view.first, disabled=False)
            view._update_button(view.last, disabled=False)
            view._update_inner_buttons(range(value - 1, value + 2))

    @property
    def current_page(self) -> str:
        return self._source[self._index]

    async def send_to(
        self,
        destination: discord.abc.Messageable,
        *,
        content: "Never" = MISSING,
        view: "Never" = MISSING,
        **kwargs,
    ):
        await self._set_index(self._index)
        self._message = await destination.send(
            content=self.current_page, view=self._view, **kwargs
        )
        return self._message


class _PageView(View):
    def __init__(self, *, parent: Pages, timeout: float | None = 180):
        super().__init__(timeout=timeout)
        self.parent = parent

    def _update_inner_buttons(self, assigned: Iterable[int]):
        bounds = range(len(self.parent._source._cache))
        for btn, idx in zip((self.left, self.center, self.right), assigned):
            if idx not in bounds:
                self._update_button(btn, label="\u200b", disabled=True)
            else:
                disable = idx == self.parent._index
                self._update_button(btn, label=idx + 1, disabled=disable)

    def _update_button(self, button: Button, *, label: object = None, disabled: bool):
        if label is not None:
            button.label = str(label)
        button.disabled = disabled
        button.style = discord.ButtonStyle.grey if disabled else discord.ButtonStyle.blurple

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.parent._author_id

    async def on_timeout(self) -> None:
        parent = self.parent
        if parent._message:
            timeout_content = parent._timeout_content
            if timeout_content is None:
                await parent._message.delete()
            elif isinstance(timeout_content, int):
                await parent._source._fill_index(timeout_content)
                await parent._message.edit(content=parent._source[timeout_content], view=None)
            else:
                await parent._message.edit(content=timeout_content, view=None)

    @button(label="≪", style=discord.ButtonStyle.blurple)
    async def first(self, interaction: discord.Interaction, button: Button):
        await self.parent._set_index(0)
        await interaction.response.edit_message(content=self.parent.current_page, view=self)

    @button(label=".", style=discord.ButtonStyle.blurple)
    async def left(self, interaction: discord.Interaction, button: Button):
        await self.parent._set_index(int(button.label) - 1)  # type: ignore
        await interaction.response.edit_message(content=self.parent.current_page, view=self)

    @button(label=".", style=discord.ButtonStyle.blurple)
    async def center(self, interaction: discord.Interaction, button: Button):
        await self.parent._set_index(int(button.label) - 1)  # type: ignore
        await interaction.response.edit_message(content=self.parent.current_page, view=self)

    @button(label=".", style=discord.ButtonStyle.blurple)
    async def right(self, interaction: discord.Interaction, button: Button):
        await self.parent._set_index(int(button.label) - 1)  # type: ignore
        await interaction.response.edit_message(content=self.parent.current_page, view=self)

    @button(label="≫", style=discord.ButtonStyle.blurple)
    async def last(self, interaction: discord.Interaction, button: Button):
        await self.parent._set_index(-1)
        await interaction.response.edit_message(content=self.parent.current_page, view=self)
