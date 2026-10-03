from __future__ import annotations

from typing import TYPE_CHECKING

from ..core import events as ev
from ..core.outcome import Outcome
from ..core.registry import register_strategy
from ..core.session import TurnJob
from ..tools.toolbox import RESEARCH
from .base import Strategy

if TYPE_CHECKING:
    from ..core.session import Session

SYSTEM = (
    "You are a careful research agent. Answer the question using the tools available. Prefer primary "
    "sources, check figures, and use run_python for any arithmetic. When you are done, call final_answer "
    "with the answer as short as possible (a number, name, date or short phrase), its unit if numeric, "
    "your confidence from 0 to 1, a one-line rationale and the source URLs you relied on."
)


def independent_prompt(question: str) -> str:
    return f"Question: {question}"


@register_strategy
class Single(Strategy):
    name = "single"
    summary = "One agent answers alone. The baseline every other strategy is measured against."
    reference = "Baseline."

    def run(self, session: Session) -> Outcome:
        agent = session.agents[0]
        session.start_round(1, "answer", [agent])
        tools = session.tools(*RESEARCH, "final_answer")
        result = session.run_turn(TurnJob(agent, SYSTEM, independent_prompt(session.question), tools, "answer"))
        if result.finished:
            p = result.payload or {}
            session.emit(ev.ROUND_CLOSED, phase="answer", decision="commit",
                         clusters=[{"id": "k1", "value": p.get("answer"), "unit": p.get("unit", ""),
                                    "support": 1, "agents": [agent.id]}])
            return Outcome("committed", str(p.get("answer")), str(p.get("unit") or ""),
                           detail={"confidence": p.get("confidence"), "sources": p.get("sources", [])})
        session.emit(ev.ROUND_CLOSED, phase="answer", decision="no_answer", clusters=[])
        return Outcome("no_answer", detail={"status": result.status, "text": result.text[:500], "error": result.error})
