"""An offline, scripted agent for demos and tests. No API keys needed.

The model field is the answer the agent gives: `mock@local:42*3` is three
agents that answer 42. When a mock agent is shown peers' answers (blackboard
revise rounds), it adopts the most common one, unless its answer ends with
`!` (stubborn), in which case it keeps its own. Each tool call is reported
as 600 input and 80 output tokens.
"""
from __future__ import annotations

import os
import re
import time
from collections import Counter
from typing import Any

from ..core.registry import register_harness
from .base import Harness, HarnessReply, TurnRequest

_ANSWERED = re.compile(r"^- (\S+) answered: (.+?)(?: — | \(sources:|$)", re.MULTILINE)


def _majority(values: list[str], own: str) -> str:
    counts = Counter(v.strip() for v in values if v.strip())
    if not counts:
        return own
    best = max(counts.values())
    if counts.get(own, 0) == best:
        return own
    return next(v for v, n in counts.items() if n == best)


def _slug(v: str) -> str:
    return re.sub(r"\W+", "-", v).strip("-") or "x"


@register_harness
class MockHarness(Harness):
    name = "mock"
    summary = "Offline scripted agent; the model field is the answer it gives (try mock@local:42*3)."
    backends = {"local": []}
    model_hints = {"local": ["42", "41", "40!"]}

    def run_turn(self, req: TurnRequest) -> HarnessReply:
        delay = float(os.environ.get("BANNAHA_MOCK_DELAY", "0") or 0)
        tools = {t.name: t for t in req.tools}
        steps = 0
        raw = req.agent.model
        stubborn = raw.endswith("!")
        value = raw.rstrip("!")

        def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            nonlocal steps
            steps += 1
            if delay:
                time.sleep(delay)
            out = req.call_tool(tools[name], args)
            req.report_usage(600 * steps, 80 * steps)
            return out

        def persuaded(values: list[str]) -> str:
            return value if stubborn else _majority(values, value)

        if "reply" in tools:  # a follow-up question about a finished run
            if "search_run" in tools:
                call("search_run", {"query": req.prompt.rsplit("USER:", 1)[-1][:80]})
            asked = req.prompt.rsplit("USER:", 1)[-1].strip()
            call("reply", {"answer": f"The run answered {value}. You asked: {asked}"})
        elif "cast_vote" in tools:
            board = call("read_board", {})
            window = board.get("vote_window") or {}
            cands = window.get("candidates", [])
            mine = [c["id"] for c in cands if c["value"].rstrip("!") == value]
            rest = [c["id"] for c in sorted(cands, key=lambda c: -len(c.get("proposed_by", []))) if c["id"] not in mine]
            call("cast_vote", {"window_id": window.get("window_id"), "ranking": mine + rest})
        elif "propose_answer" in tools:
            board = call("read_board", {})
            chosen = value
            earlier = [p for p in board["proposals"] if p["round"] < board["round"]]
            if earlier:
                last = max(p["round"] for p in earlier)
                chosen = persuaded([p["value"] for p in earlier if p["round"] == last])
            posted = call("post_claim", {"value": chosen, "quote": f"Mock evidence that the answer is {chosen}.",
                                         "source": f"https://example.org/mock/{_slug(chosen)}/{req.agent.id}",
                                         "confidence": 0.7})
            for claim in board["claims"][-3:]:
                if claim["agent"] == req.agent.id:
                    continue
                if claim["value"] == chosen:
                    call("endorse_claim", {"claim_id": claim["claim_id"], "note": "matches my evidence"})
                else:
                    call("challenge_claim", {"claim_id": claim["claim_id"], "reason": "conflicts with my evidence",
                                             "counter_value": chosen})
            call("propose_answer", {"value": chosen, "rationale": "mock reasoning",
                                    "claim_ids": [posted.get("claim_id")] if posted.get("claim_id") else []})
        elif "final_answer" in tools:
            peers = [v for _, v in _ANSWERED.findall(req.prompt)]
            answer = persuaded(peers) if peers else value
            call("final_answer", {"answer": answer, "confidence": 0.7, "rationale": "mock reasoning"})
        return HarnessReply(steps=steps, input_tokens=600 * steps, output_tokens=80 * steps)
