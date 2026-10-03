from __future__ import annotations

from typing import TYPE_CHECKING

from ..consensus.clustering import cluster_answers
from ..core import events as ev
from ..core.outcome import Outcome
from ..core.registry import register_strategy
from ..core.session import TurnJob
from ..tools.toolbox import RESEARCH
from .base import Option, Strategy
from .single import SYSTEM, independent_prompt

if TYPE_CHECKING:
    from ..core.session import Session


@register_strategy
class Vote(Strategy):
    name = "vote"
    summary = "Agents answer independently and never see each other; the most common answer wins."
    reference = "Self-consistency: Wang et al., 2022 (arXiv:2203.11171)."
    min_agents = 2
    options = {"tolerance": Option(float, 0.005, "relative tolerance when grouping numeric answers")}

    def run(self, session: Session) -> Outcome:
        agents = session.alive()
        session.start_round(1, "answer", agents)
        tools = session.tools(*RESEARCH, "final_answer")
        prompt = independent_prompt(session.question)
        results = session.run_turns([TurnJob(a, SYSTEM, prompt, tools, "answer") for a in agents])
        answers = [(r.agent, str(r.payload.get("answer")), str(r.payload.get("unit") or ""))
                   for r in results if r.finished and r.payload]
        statuses = {r.agent: r.status for r in results}
        if not answers:
            session.emit(ev.ROUND_CLOSED, phase="answer", decision="no_answer", clusters=[])
            return Outcome("no_answer", detail={"statuses": statuses})
        clusters = cluster_answers(answers, rel_tol=self.opts["tolerance"])
        top = clusters[0]
        tie = len(clusters) > 1 and clusters[1].support == top.support
        session.emit(ev.ROUND_CLOSED, phase="answer", decision="commit", clusters=[c.view() for c in clusters],
                     answered=len(answers), tie=tie)
        return Outcome("committed", top.display, top.unit, detail={
            "agreement": round(top.support / len(answers), 3), "answered": len(answers),
            "agents": len(agents), "tie": tie, "clusters": [c.view() for c in clusters], "statuses": statuses,
        })
