import discord
from itertools import chain
from typing import (
    Dict,
    Generator,
    Hashable,
    Tuple,
    TypeVar,
    Set,
    Union,
)

from .irc import IRCMessageable


T = TypeVar("T", bound=Hashable)
Vector = Tuple[T, T]  # ORDER MATTERS
Messageable = Union[discord.abc.Messageable, IRCMessageable]
User = Union[discord.User, IRCMessageable]


class GraphError(Exception):
    pass


class SimpleGraph(Dict[T, Set[T]]):
    def add_web(self, *vertices: T) -> None:
        """
        Opens up all possible connections between the specified vertices.
        """
        for vertex in vertices:
            self.add_vectors(vertex, *vertices)

    def remove_vertices(self, *vertices: T) -> None:
        """
        Removes all connections to and from the specified vertices.
        """
        v_set = set(vertices)
        for vertex, neighbors in self.copy().items():  # type: ignore
            if vertex in v_set:
                self.pop(vertex)
            else:
                neighbors -= v_set

    def add_vectors(self, a: T, *b_s: T, two_way: bool = False) -> None:
        b_set = set(b_s)
        b_set.discard(a)
        self.setdefault(a, set()).update(b_set)
        if two_way:
            for b in b_set:
                self.setdefault(b, set()).add(a)

    def remove_vectors(self, a: T, *b_s: T, two_way: bool = False) -> None:
        b_set = set(b_s)
        b_set.discard(a)
        self.setdefault(a, set()).difference_update(b_set)
        if two_way:
            for b in b_set:
                self.setdefault(b, set()).discard(a)

    def is_vector(self, a: T, b: T, *, two_way: bool = False) -> bool:
        if two_way:
            return b in self.get(a, ()) and a in self.get(b, ())
        return b in self.get(a, ())

    def vertices(self) -> Set[T]:
        keys = set(filter(self.__getitem__, self.keys()))
        return keys.union(chain.from_iterable(self.values()))

    def vectors(self) -> Generator[Vector[T], None, None]:
        for vertex, neighbors in self.items():
            for neighbor in neighbors:
                yield vertex, neighbor

    def to_json(self):
        return {k: list(v) for k, v in self.items()}

    @classmethod
    def from_json(cls, json):
        return cls((k, set(v)) for k, v in json.items())
