from collections.abc import Sequence
from itertools import chain, repeat


# from https://stackoverflow.com/questions/2970608/what-are-named-tuples-in-python
class NamedList(Sequence):
    """Abstract Base Class for objects that work like mutable
    namedtuples. Subclass and define your named fields with
    __slots__ and away you go.
    """

    __slots__ = ()

    def __init__(self, *args, default=None):
        for slot, arg in zip(self.__slots__, chain(args, repeat(default))):
            setattr(self, slot, arg)

    def __repr__(self):
        return type(self).__name__ + repr(tuple(self))

    # more direct __iter__ than Sequence's
    def __iter__(self):
        for name in self.__slots__:
            yield getattr(self, name)

    # Sequence requires __getitem__ & __len__:
    def __getitem__(self, index):
        if isinstance(index, slice):
            return [getattr(self, self.__slots__[a]) for a in range(len(self.__slots__))[index]]
        return getattr(self, self.__slots__[index])

    def __len__(self):
        return len(self.__slots__)
