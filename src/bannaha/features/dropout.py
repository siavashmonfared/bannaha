from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from ..core import events as ev
from ..core.errors import AgentDropped
from ..core.registry import register_feature
from .base import Feature

if TYPE_CHECKING:
    from ..core.session import Session
    from ..core.tools import JsonDict, Tool, ToolContext


@register_feature
class Dropout(Feature):
    """Fault injection: silently remove agents from the fleet mid-run.

    `amount` below 1 is a fraction of the eligible agents, otherwise a
    count. A chosen agent goes dark when it tries its tool call number
    `after_calls + 1`; from then on it never responds. Which agents are
    eligible is up to the strategy (by default, every agent).
    """

    name = "dropout"
    summary = "remove agents mid-run (fault injection)"

    def __init__(self, amount: float, after_calls: int = 2, seed: int | None = None) -> None:
        self.amount = amount
        self.after_calls = max(0, int(after_calls))
        self.seed = seed
        self.victims: set[str] = set()

    def on_session_start(self, session: Session) -> None:
        eligible = [a.id for a in session.strategy.dropout_eligible(session.agents)]
        n = round(self.amount * len(eligible)) if self.amount < 1 else int(self.amount)
        n = max(0, min(n, len(eligible)))
        self.victims = set(random.Random(self.seed).sample(eligible, n)) if n else set()

    def before_tool(self, ctx: ToolContext, tool: Tool, args: JsonDict) -> None:
        agent = ctx.agent
        if agent.id in self.victims and agent.alive and agent.stats.tool_calls >= self.after_calls:
            agent.alive = False
            ctx.session.emit(ev.AGENT_DROPPED, agent=agent.id, after_calls=agent.stats.tool_calls)
        if not agent.alive:
            raise AgentDropped(agent.id)

    def describe(self) -> str:
        amount = f"{self.amount:.0%}" if self.amount < 1 else str(int(self.amount))
        return f"dropout {amount} after {self.after_calls} calls"

    def config(self) -> dict[str, Any]:
        return {"amount": self.amount, "after_calls": self.after_calls, "seed": self.seed}
