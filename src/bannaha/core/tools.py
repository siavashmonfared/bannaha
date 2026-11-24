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
    tool_errors: int = 0  # tool calls this turn that returned ok=False
    consecutive_errors: int = 0  # failed calls since the last successful one
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


def coerce_args(parameters: JsonDict, args: JsonDict) -> JsonDict:
    """Repair argument types small models get wrong, guided by the tool's JSON Schema: "5" for an integer,
    "0.7" for a number, "true" for a boolean, a JSON-encoded string or "a, b" for an array, a JSON-encoded
    string for an object. Values that cannot be repaired are left as they are for the handler to judge."""
    import json

    props = (parameters or {}).get("properties") or {}
    out = dict(args)
    for key, value in args.items():
        typ = (props.get(key) or {}).get("type")
        if not isinstance(value, str) or typ in (None, "string"):
            continue
        text = value.strip()
        try:
            if typ == "integer" and text.lstrip("+-").isdigit():
                out[key] = int(text)
            elif typ == "number":
                out[key] = float(text)
            elif typ == "boolean" and text.lower() in ("true", "false"):
                out[key] = text.lower() == "true"
            elif typ == "array":
                parsed = json.loads(text) if text.startswith("[") else None
                out[key] = parsed if isinstance(parsed, list) else [p.strip() for p in text.split(",") if p.strip()]
            elif typ == "object" and text.startswith("{"):
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    out[key] = parsed
        except (ValueError, TypeError):
            continue
    return out
