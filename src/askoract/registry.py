"""A tiny name -> object registry, the extension point for every pluggable component.

Databases, datasets, benchmarks, tools, and user channels each have one registry.
Adding an implementation means writing a class and registering it under a name;
configs then select it by that name.
"""

from collections.abc import Callable, Iterator


class Registry[T]:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def add(self, name: str, item: T) -> T:
        if name in self._items:
            raise ValueError(f"{self.kind} {name!r} is already registered")
        self._items[name] = item
        return item

    def register(self, name: str) -> Callable[[T], T]:
        """Decorator form: `@TOOLS.register("run_sql")`."""
        return lambda item: self.add(name, item)

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            available = ", ".join(sorted(self._items)) or "(none)"
            raise KeyError(f"unknown {self.kind} {name!r}; available: {available}") from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())
