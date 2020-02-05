import discord
from itertools import chain
from typing import (
    Dict,
    DefaultDict,
    Generator,
    Hashable,
    Iterable,
    Optional,
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
        for vertex, neighbors in self.copy().items():
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
        for vertex, neighbors in self.copy().items():
            for neighbor in neighbors.copy():
                yield vertex, neighbor


class Graph(SimpleGraph[Messageable]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lists = DefaultDict[
            Optional[Vector[Messageable]],  # None means global
            Tuple[Set[User], Set[User]],  # False: Blacklist, True: Whitelist
        ](lambda: (set(), set()))
        self.messages = SimpleGraph[discord.Message]()

    @staticmethod
    def _combine(one, many):
        if one and many:
            return set((one, *many))
        if one and not many:
            return {one}
        if not one and many:
            return set(many)
        return set()

    def _list(self, worb: bool, vector: Vector[Messageable], add, remove):
        l = self._lists[vector][worb]
        if add == remove:
            add, remove = (), ()
        if add:
            l.update(add)
        if remove:
            l.difference_update(add)
        return l.copy()

    def is_allowed(self, *args: Messageable, user: User):
        if len(args) not in (0, 2):
            raise TypeError(f"Unexpected number of positional arguments: {len(args)}")
        vector = args or None
        if vector:
            lists = [self._lists[None], self._lists[vector]]
        else:
            lists = [self._lists[vector]]
        for l in lists:
            if l[True]:
                if user not in l[True]:
                    return False
            else:
                if user in l[False]:
                    return False
        return True

    def whitelist(
        self,
        *args: Messageable,
        add: User = None,
        add_all: Iterable[User] = None,
        remove: User = None,
        remove_all: Iterable[User] = None,
    ):
        if len(args) not in (0, 2):
            raise TypeError(f"Unexpected number of positional arguments: {len(args)}")
        return self._list(
            True, args or None, self._combine(add, add_all), self._combine(remove, remove_all)
        )

    def blacklist(
        self,
        *args: Messageable,
        add: User = None,
        add_all: Iterable[User] = None,
        remove: User = None,
        remove_all: Iterable[User] = None,
    ):
        if len(args) not in (0, 2):
            raise TypeError(f"Unexpected number of positional arguments: {len(args)}")
        return self._list(
            False, args or None, self._combine(add, add_all), self._combine(remove, remove_all)
        )
