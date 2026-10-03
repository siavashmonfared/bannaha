"""Follow-ups: keep talking about a run after it finishes.

When a run is done, what you type next goes to one agent (by default the strongest model in the fleet)
together with the run's memory: the task, how the strategy decided, what every agent submitted, the key
tool results, and a search over the full trace. The conversation builds turn by turn and is saved with
the run (followups.jsonl), so `bannaha ask <run folder>` can pick it up later.

    /new <task>          start a fresh run that knows nothing of this one
    /rerun <text>        run the same strategy and fleet again, told the previous answer and your clarification
    /to <agent>          send follow-ups to another fleet agent (a3) or any model (harness@backend:model)
    /agents              list the fleet and who answers follow-ups
    /forget              clear the follow-up conversation (the run's memory stays)
    /help                all commands and keys
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import events as ev
from .core.errors import BannahaError
from .core.events import Event
from .core.fleet import parse_fleet
from .core.outcome import Outcome
from .core.pricing import price_for
from .core.session import Session, TurnJob, check_fleet
from .core.text import clip
from .core.tools import JsonDict, Tool, ToolContext, schema
from .strategies.base import Strategy
from .tools.toolbox import RESEARCH, ToolOptions

COMMANDS = [
    ("/help", "Show these commands and keys.", "/help"),
    ("/new <task>", "Start a fresh run on a new task. It knows nothing of the current run.", "/new What is 17^5?"),
    ("/rerun <clarification>", "Run the same strategy and fleet again on the same task, telling the agents the "
                               "previous answer and your clarification. Use it when the clarification changes "
                               "the task rather than just asking about it.", "/rerun use 2024 figures only"),
    ("/to <agent>", "Send follow-ups to another agent: a fleet id, or any model as harness@backend:model.",
     "/to a3  ·  /to anthropic@direct:claude-opus-4-6"),
    ("/agents", "List the fleet and show who answers follow-ups.", "/agents"),
    ("/forget", "Clear the follow-up conversation. The run's memory stays.", "/forget"),
    ("(plain text)", "After a run: a follow-up question about it, with the run as memory. Before any run: a "
                     "new task.", "why did a2 answer 669?"),
]
KEYS = [
    ("Enter", "send the text in the box"),
    ("↑ / ↓", "earlier questions"),
    ("Esc", "stop a running task; leave the input box"),
    ("ctrl+o", "show tool results in full / shortened"),
    ("ctrl+g", "follow one agent at a time in the transcript"),
    ("ctrl+t", "switch between the transcript and the dashboard"),
    ("ctrl+b", "hide or show the banner"),
    ("1 – 5", "Setup, Fleet, Keys, Run, Runs pages (when the box is not focused)"),
]
FINISHERS = {"final_answer", "propose_answer", "cast_vote"}


def parse_command(text: str) -> tuple[str | None, str]:
    """('/rerun', 'x y') for '/rerun x y'; (None, text) for plain text."""
    text = text.strip()
    if not text.startswith("/"):
        return None, text
    head, _, rest = text.partition(" ")
    return head.lower(), rest.strip()


def strongest(labels: list[str]) -> str:
    """The fleet member with the highest output price per token (a proxy for capability); the first on ties."""
    best, best_price = labels[0], -1.0
    for label in labels:
        try:
            e = parse_fleet(label)[0]
            p = price_for(e.backend, e.model)
        except Exception:  # pricing is best effort
            p = None
        price = p.output_per_tok if p else 0.0
        if price > best_price:
            best, best_price = label, price
    return best


# -- memory -----------------------------------------------------------------------------------------------------

@dataclass
class RunMemory:
    question: str
    strategy: str
    fleet: list[dict[str, Any]]
    outcome: dict[str, Any]
    submissions: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    run_dir: Path | None = None

    @classmethod
    def from_events(cls, events: list[Event], run_dir: Path | None = None) -> RunMemory:
        from .cli.live import describe_round, tool_brief

        info: dict[str, Any] = {}
        outcome: dict[str, Any] = {}
        subs, decisions, results, texts = [], [], [], []
        pending: dict[str, list[dict[str, Any]]] = {}
        rnd, phase = None, ""
        for e in events:
            d = e.data
            if e.kind == ev.SESSION_STARTED:
                info = d
            elif e.kind == ev.ROUND_STARTED:
                rnd, phase = e.round, d.get("phase", "")
            elif e.kind == ev.TOOL_CALLED and e.agent:
                pending.setdefault(e.agent, []).append(d)
            elif e.kind == ev.TOOL_RETURNED and e.agent:
                queue = pending.get(e.agent, [])
                call = next((c for c in queue if c.get("tool") == d.get("tool")), None)
                if call:
                    queue.remove(call)
                tool = d.get("tool", "")
                where = f"[round {rnd} · {phase}] {e.agent}"
                if tool in FINISHERS and d.get("ok"):
                    subs.append(f"- {where} {tool}: {clip((call or {}).get('full') or '', 3000)}")
                elif call:
                    preview = clip(d.get("preview") or d.get("error") or "", 700)
                    results.append(f"- {where} {tool} {tool_brief(tool, call.get('full'), call.get('args'))} → "
                                   f"{preview}")
            elif e.kind == ev.TURN_FINISHED and e.agent and (d.get("text") or "").strip():
                texts.append(f"- [round {rnd} · {phase}] {e.agent}: {clip(d['text'], 1500)}")
            elif e.kind == ev.ROUND_CLOSED:
                decisions.append("- " + describe_round(d, e.round))
            elif e.kind == ev.SESSION_FINISHED:
                outcome = d.get("outcome") or {}
        return cls(question=info.get("question", ""), strategy=info.get("strategy", ""), fleet=info.get("fleet", []),
                   outcome=outcome, submissions=subs, decisions=decisions, tool_results=results, texts=texts,
                   run_dir=run_dir)

    @classmethod
    def from_dir(cls, run_dir: Path) -> RunMemory:
        from .cli.transcript import load_events

        events = load_events(run_dir / "events.jsonl")
        if not events:
            raise BannahaError(f"no run log in {run_dir}")
        return cls.from_events(events, run_dir)

    def labels(self) -> list[str]:
        return [a["label"] for a in self.fleet]

    def context(self, limit: int = 60_000) -> str:
        o = self.outcome
        detail = o.get("detail") or {}
        head = [f"THE TASK:\n{self.question}", "",
                f"STRATEGY: {self.strategy}",
                "FLEET:\n" + "\n".join(f"  {a['id']}: {a['label']}" for a in self.fleet), "",
                f"OUTCOME: {o.get('status')} · answer: {o.get('answer')} {o.get('unit') or ''}".rstrip(),
                *([f"  decided by: {detail['decided_by']}"] if detail.get("decided_by") else []),
                *([f"  reason: {detail['reason']}"] if detail.get("reason") else [])]
        budget = max(5000, limit - sum(len(x) for x in head))
        parts = [("WHAT EACH AGENT SUBMITTED (in order):", self.submissions, 0.45),
                 ("ROUND DECISIONS:", self.decisions, 0.1),
                 ("KEY TOOL RESULTS (trimmed; newest last):", self.tool_results, 0.3),
                 ("WHAT AGENTS SAID OUTSIDE TOOLS:", self.texts, 0.15)]
        out = head
        for title, items, share in parts:
            if not items:
                continue
            room, chosen = int(budget * share), []
            for item in reversed(items):  # keep the latest when the run is long
                if room - len(item) < 0:
                    chosen.append(f"- … {len(items) - len(chosen)} earlier item(s) left out; use search_run")
                    break
                chosen.append(item)
                room -= len(item)
            out += ["", title, *reversed(chosen)]
        return "\n".join(out)

    def search(self, query: str, k: int = 6) -> list[str]:
        stop = {"the", "and", "why", "did", "was", "what", "how", "who", "one", "say", "said", "are", "you"}
        words = [w for w in re.findall(r"\w+", query.lower()) if (len(w) > 2 or w.isdigit()) and w not in stop]
        items = self.submissions + self.tool_results + self.texts + self.decisions
        if not words:
            return items[-k:]
        scored = sorted(items, key=lambda x: -sum(x.lower().count(w) for w in words))
        return [x for x in scored[:k] if any(w in x.lower() for w in words)]


def rerun_question(mem: RunMemory, clarification: str) -> str:
    o = mem.outcome
    answer = f"{o.get('answer')} {o.get('unit') or ''}".strip() if o.get("answer") is not None else "no answer"
    return (f"{mem.question}\n\nA previous run on this task ({mem.strategy}) answered: {answer}.\n"
            f"The user adds this clarification: {clarification}\n"
            "Take the clarification into account. Reuse or overturn the previous answer as the evidence warrants.")


# -- the follow-up agent ------------------------------------------------------------------------------------------

SYSTEM = (
    "You are {model}, answering a user's follow-up questions about a multi-agent run that has finished. The run's "
    "memory is below: the task, how the agents worked, what each one submitted, and the final answer. Answer the "
    "user directly and honestly: say what the run found, how it got there, where agents disagreed, and what is "
    "uncertain. If the memory does not settle the question, check with your tools (search_run searches the run's "
    "full trace; web search and python when available) instead of guessing. You may disagree with the run's "
    "answer when the evidence supports it. Finish with the reply tool."
)


class FollowUpStrategy(Strategy):
    name = "followup"
    summary = "Answer questions about a finished run."
    min_agents = 1

    def __init__(self, memory: RunMemory, history: list[dict[str, str]]) -> None:
        super().__init__()
        self.memory, self.history = memory, history

    def run(self, session: Session) -> Outcome:
        mem = self.memory

        def search_run(ctx: ToolContext, args: JsonDict) -> JsonDict:
            return {"ok": True, "matches": mem.search(str(args.get("query") or ""), int(args.get("k") or 6))}

        def reply(ctx: ToolContext, args: JsonDict) -> JsonDict:
            return {"ok": bool(str(args.get("answer") or "").strip()), "error": None if args.get("answer") else
                    "answer is required"}

        session.toolbox.add(Tool("search_run", "Search the finished run's full trace: every submission, tool result "
                                 "and decision. Returns the best-matching entries.",
                                 schema({"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"]),
                                 search_run))
        session.toolbox.add(Tool("reply", "Your answer to the user's follow-up question (Markdown). Ends your turn.",
                                 schema({"answer": {"type": "string"},
                                         "sources": {"type": "array", "items": {"type": "string"}}}, ["answer"]),
                                 reply, terminal=True))
        convo = "\n".join(f"{h['role'].upper()}: {h['text']}" for h in self.history[-12:])
        prompt = (f"RUN MEMORY\n==========\n{mem.context()}\n\n"
                  + (f"CONVERSATION SO FAR\n===================\n{convo}\n\n" if convo else "")
                  + f"USER: {session.question}")
        agent = session.agents[0]
        session.start_round(1, "follow-up", [agent])
        res = session.run_turn(TurnJob(agent, SYSTEM.format(model=agent.model), prompt,
                                       session.tools(*RESEARCH, "search_run", "reply"), "follow-up"))
        if res.finished and res.payload:
            return Outcome("committed", str(res.payload.get("answer")),
                           detail={"sources": res.payload.get("sources") or []})
        text = res.text.strip()
        return Outcome("committed" if text else "no_answer", text or None,
                       detail={"status": res.status, "error": res.error})


class FollowUp:
    """A follow-up conversation about one run."""

    def __init__(self, memory: RunMemory, *, agent: str | None = None) -> None:
        self.memory = memory
        self.agent = agent or (strongest(memory.labels()) if memory.fleet else "")
        self.history: list[dict[str, str]] = []
        self.load()

    # which agent answers ------------------------------------------------------------------------------------------

    def agent_id(self) -> str:
        return next((a["id"] for a in self.memory.fleet if a["label"] == self.agent), "ask")

    def set_agent(self, spec: str) -> str:
        spec = spec.strip()
        by_id = {a["id"]: a["label"] for a in self.memory.fleet}
        label = by_id.get(spec, spec)
        problems = check_fleet(parse_fleet(label))
        if problems:
            raise BannahaError("; ".join(problems))
        self.agent = label
        return label

    # one turn -------------------------------------------------------------------------------------------------

    def session(self, question: str, tool_options: ToolOptions, limits=None) -> Session:
        entry = parse_fleet(self.agent)[0]
        s = Session(question=question, fleet=[entry], strategy=FollowUpStrategy(self.memory, self.history),
                    tool_options=tool_options, limits=limits)
        s.agents[0].id = self.agent_id()
        if self.memory.run_dir:
            log = (self.memory.run_dir / "followup_events.jsonl").open("a", encoding="utf-8")
            s.bus.subscribe(lambda e: (log.write(json.dumps(e.to_dict(), ensure_ascii=False, default=str) + "\n"),
                                       log.flush()))
        return s

    def record(self, question: str, outcome: Outcome | None, cost: float | None = None) -> None:
        answer = (outcome.answer if outcome else None) or "(no answer)"
        self.history += [{"role": "user", "text": question}, {"role": "assistant", "text": answer}]
        if self.memory.run_dir:
            with (self.memory.run_dir / "followups.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(time.time(), 1), "agent": self.agent, "question": question,
                                     "answer": answer, "cost_usd": cost}, ensure_ascii=False) + "\n")

    def load(self) -> None:
        f = self.memory.run_dir / "followups.jsonl" if self.memory.run_dir else None
        if f and f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.history += [{"role": "user", "text": d["question"]}, {"role": "assistant", "text": d["answer"]}]

    def forget(self) -> None:
        self.history = []


def help_renderable(theme) -> Any:
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    t1 = Table(box=None, pad_edge=False, show_header=True, header_style=theme.muted, padding=(0, 3, 0, 0))
    t1.add_column("command", style=f"bold {theme.accent}", no_wrap=True)
    t1.add_column("what it does")
    t1.add_column("example", style=theme.muted)
    for cmd, what, example in COMMANDS:
        t1.add_row(cmd, what, example)
    t2 = Table(box=None, pad_edge=False, show_header=True, header_style=theme.muted, padding=(0, 3, 0, 0))
    t2.add_column("key", style=f"bold {theme.accent}", no_wrap=True)
    t2.add_column("what it does")
    for key, what in KEYS:
        t2.add_row(key, what)
    return Group(Text("Commands", "bold"), t1, Text(""), Text("Keys (full-screen app)", "bold"), t2)
