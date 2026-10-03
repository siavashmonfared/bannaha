"""Plug-in registry for strategies, harnesses and features.

Built-ins are discovered by importing every module in their package, so
adding one is "drop a file in the folder" and removing one is "delete the
file". Third-party packages can register more through entry points:

    [project.entry-points."bannaha.strategies"]
    my_strategy = "my_pkg.my_strategy"
"""
from __future__ import annotations

import importlib
import pkgutil
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from ..features.base import Feature
    from ..harnesses.base import Harness
    from ..strategies.base import Strategy

T = TypeVar("T")

_STRATEGIES: dict[str, type[Strategy]] = {}
_HARNESSES: dict[str, type[Harness]] = {}
_FEATURES: dict[str, type[Feature]] = {}
_loaded: set[str] = set()


def register_strategy(cls: type[T]) -> type[T]:
    _STRATEGIES[cls.name] = cls  # type: ignore[attr-defined]
    return cls


def register_harness(cls: type[T]) -> type[T]:
    _HARNESSES[cls.name] = cls  # type: ignore[attr-defined]
    return cls


def register_feature(cls: type[T]) -> type[T]:
    _FEATURES[cls.name] = cls  # type: ignore[attr-defined]
    return cls


def _discover(package: str, group: str) -> None:
    if package in _loaded:
        return
    _loaded.add(package)
    pkg = importlib.import_module(package)
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.name.startswith("_") and info.name != "base":
            importlib.import_module(f"{package}.{info.name}")
    for ep in entry_points(group=group):
        ep.load()


def strategies() -> dict[str, type[Strategy]]:
    _discover("bannaha.strategies", "bannaha.strategies")
    return dict(sorted(_STRATEGIES.items()))


def harnesses() -> dict[str, type[Harness]]:
    _discover("bannaha.harnesses", "bannaha.harnesses")
    return dict(sorted(_HARNESSES.items()))


def features() -> dict[str, type[Feature]]:
    _discover("bannaha.features", "bannaha.features")
    return dict(sorted(_FEATURES.items()))
