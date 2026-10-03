from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.board import PEER_DATA_NOTE
from ..core.registry import register_feature
from .base import Feature

if TYPE_CHECKING:
    from ..core.tools import JsonDict, Tool, ToolContext


@register_feature
class PeerUpdates(Feature):
    """Attach other agents' new board activity to every tool result.

    Gives agents ambient awareness of each other mid-turn without any
    harness changes. Only applies to strategies that use the board.
    """

    name = "peer_updates"
    summary = "attach peers' new board activity to each tool result"

    def __init__(self, limit: int = 8) -> None:
        self.limit = limit

    def after_tool(self, ctx: ToolContext, tool: Tool, args: JsonDict, result: JsonDict) -> JsonDict:
        if not ctx.session.strategy.uses_board or tool.name == "read_board":
            return result
        items, version = ctx.session.board.changes_since(
            ctx.agent.last_seen_version, exclude=ctx.agent.id, limit=self.limit)
        ctx.agent.last_seen_version = version
        if items:
            result = {**result, "peer_updates": {"note": PEER_DATA_NOTE, "items": items}}
        return result

    def config(self) -> dict[str, Any]:
        return {"limit": self.limit}
