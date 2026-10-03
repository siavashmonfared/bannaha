"""The strategy interface.

A strategy decides who runs, with which tools and prompts, and how their
outputs become one answer. It talks to the world only through the session
(`run_turns`, `tools`, `board`, `emit`), so it works with any harness.

To add one: create a module in this package with a `Strategy` subclass
decorated with `@register_strategy`. Options declared in `options` can be
set from the CLI with `--opt key=value`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from ..core.errors import BannahaError

if TYPE_CHECKING:
    from ..core.fleet import Agent
    from ..core.outcome import Outcome
    from ..core.session import Session


@dataclass(frozen=True)
class Option:
    type: type
    default: Any
    help: str


class Strategy(ABC):
    name: ClassVar[str]
    summary: ClassVar[str] = ""
    reference: ClassVar[str] = ""
    uses_board: ClassVar[bool] = False
    min_agents: ClassVar[int] = 1
    options: ClassVar[dict[str, Option]] = {}

    def __init__(self, **opts: Any) -> None:
        unknown = set(opts) - set(self.options)
        if unknown:
            raise BannahaError(f"unknown option(s) for {self.name}: {', '.join(sorted(unknown))}; "
                               f"known: {', '.join(self.options) or 'none'}")
        self.opts: dict[str, Any] = {}
        for key, opt in self.options.items():
            raw = opts.get(key, opt.default)
            self.opts[key] = coerce(raw, opt.type, key)

    def assign_roles(self, agents: list[Agent]) -> None:
        for a in agents:
            a.role = "peer"

    def dropout_eligible(self, agents: list[Agent]) -> list[Agent]:
        return list(agents)

    @abstractmethod
    def run(self, session: Session) -> Outcome:
        ...


def coerce(raw: Any, typ: type, key: str) -> Any:
    if isinstance(raw, typ) and not (typ is int and isinstance(raw, bool)):
        return raw
    if typ is bool:
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        raise BannahaError(f"option {key} expects true/false, got {raw!r}")
    try:
        return typ(raw)
    except (TypeError, ValueError) as exc:
        raise BannahaError(f"option {key} expects {typ.__name__}, got {raw!r}") from exc
