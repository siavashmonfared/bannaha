"""One tool spec shared by every harness.

A tool is a name, a description, a JSON Schema for its arguments and a
handler `(ctx, args) -> dict`. Harness adapters translate the schema into
their SDK's format; the handler always receives a `ToolContext`, so tools
know which agent is calling without per-harness closures.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .fleet import Agent
    from .session import Session

JsonDict = dict[str, Any]


@dataclass
class TurnState:
    phase: str = ""
    terminals: tuple[str, ...] = ()
    max_tool_calls: int = 16
    tool_calls: int = 0
    terminal_tool: str | None = None
    terminal_args: JsonDict | None = None
    stale_rejections: int = 0
    tools_used: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.terminal_tool is not None


@dataclass
class ToolContext:
    session: Session
    agent: Agent
    turn: TurnState = field(default_factory=TurnState)


Handler = Callable[[ToolContext, JsonDict], JsonDict]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: JsonDict
    handler: Handler
    terminal: bool = False


def schema(properties: dict[str, JsonDict], required: list[str] | None = None) -> JsonDict:
    return {"type": "object", "properties": properties, "required": required or []}
