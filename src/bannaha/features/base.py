"""Session-level features: small hooks around every tool call.

A feature can stop a call before it runs (`before_tool` raising a
`TurnControl` error) or decorate its result (`after_tool`). Strategies
don't know which features are on.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from ..core.session import Session
    from ..core.tools import JsonDict, Tool, ToolContext


class Feature:
    name: ClassVar[str] = "base"
    summary: ClassVar[str] = ""

    def on_session_start(self, session: Session) -> None:
        pass

    def before_tool(self, ctx: ToolContext, tool: Tool, args: JsonDict) -> None:
        pass

    def after_tool(self, ctx: ToolContext, tool: Tool, args: JsonDict, result: JsonDict) -> JsonDict:
        return result

    def describe(self) -> str:
        return self.name

    def config(self) -> dict[str, Any]:
        return {}
