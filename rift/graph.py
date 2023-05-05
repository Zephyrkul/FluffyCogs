from itertools import chain
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Dict,
    Generator,
    Generic,
    Hashable,
    MutableMapping,
    Set,
    Tuple,
    Type,
    TypeVar,
)

__all__ = ["GraphError", "SimpleGraph", "Vector"]
T = TypeVar("T", bound=Hashable)
Vector = Tuple[T, T]  # ORDER MATTERS


class GraphError(Exception):
    pass


if TYPE_CHECKING:
    _Base = MutableMapping[T, Set[T]]
else:
    _Base = Generic


class GraphMixin(_Base[T]):
    _set: ClassVar[Type[Set[T]]]

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
        self.setdefault(a, self._set()).update(b_set)  # type: ignore
        if two_way:
            for b in b_set:
                self.setdefault(b, self._set()).add(a)

    def remove_vectors(self, a: T, *b_s: T, two_way: bool = False) -> None:
        b_set = set(b_s)
        b_set.discard(a)
        self.setdefault(a, self._set()).difference_update(b_set)  # type: ignore
        if two_way:
            for b in b_set:
                self.setdefault(b, self._set()).discard(a)

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
        return cls((k, cls._set(v)) for k, v in json.items())


class SimpleGraph(GraphMixin[T], Dict[T, Set[T]]):
    _set = set
