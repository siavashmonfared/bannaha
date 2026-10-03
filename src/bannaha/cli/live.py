"""The Rich live view, and a plain line-per-event printer for logs and CI.

Both are event-stream subscribers: they rebuild their state from events and
never reach into strategies or harnesses.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..core import events as ev
from ..core.events import Event
from ..core.outcome import Outcome
from ..core.session import Session
from ..core.text import clip, tilde
from .themes import Theme

STATUS = {
    "idle": ("·", "muted"), "thinking": ("◐", "accent"), "tool": ("⚙", "tool"), "done": ("✓", "ok"),
    "dropped": ("✕", "err"), "error": ("!", "err"), "limit": ("⌛", "warn"), "stopped": ("■", "warn"),
    "no_terminator": ("?", "warn"), "budget": ("$", "warn"),
}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
RECENT = 4  # steps shown per agent in the activity panel
OUTCOME = {"committed": ("✔", "ok"), "no_answer": ("∅", "warn"), "failed": ("✘", "err"),
           "stopped": ("■", "warn"), "error": ("!", "err")}


def _domain(url: str) -> str:
    s = url.split("://", 1)[-1]
    return s.split("/", 1)[0][:40]


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _fmt_cost(x: float | None) -> str:
    if x is None:
        return "$?"
    return f"${x:.4f}" if x < 0.01 else (f"${x:.3f}" if x < 1 else f"${x:,.2f}")


def tool_brief(tool: str, full: str | None, summary: str | None = None) -> str:
    """What a tool call is doing, in a few words: the query, the page, the first line of code."""
    try:
        args = json.loads(full or "")
    except (TypeError, ValueError):
        args = None
    if not isinstance(args, dict):
        return clip(summary or "", 80)
    if tool in ("web_search", "search_documents", "search_run"):
        return f'"{clip(args.get("query"), 70)}"'
    if tool == "fetch_page":
        return clip(str(args.get("url", "")).split("://", 1)[-1], 70)
    if tool == "run_python":
        lines = [ln.strip() for ln in str(args.get("code", "")).splitlines()]
        code = [ln for ln in lines if ln and not ln.startswith("#")]
        return clip(code[0] if code else "", 70) + (f"  (+{len(code) - 1} lines)" if len(code) > 1 else "")
    if tool == "send_message":
        return f"→ {args.get('to') or 'all'}: {clip(args.get('text'), 60)}"
    for key in ("answer", "value", "ranking", "claim_id"):
        if args.get(key) not in (None, ""):
            return clip(args[key] if isinstance(args[key], str) else json.dumps(args[key]), 80)
    return clip(summary or "", 80)


def describe_event(e: Event) -> str | None:
    """One human-readable line for the board ledger / plain printer, or None."""
    d, a = e.data, e.agent or ""
    k = e.kind
    if k == ev.CLAIM_POSTED:
        unit = f" {d.get('unit')}" if d.get("unit") else ""
        return f"{a} ▸ claim {d.get('claim_id')}: {clip(d.get('value'), 40)}{unit}  ({_domain(d.get('source', ''))})"
    if k == ev.CLAIM_ENDORSED:
        return f"{a} ✓ endorses {d.get('claim_id')}"
    if k == ev.CLAIM_CHALLENGED:
        return f"{a} ✗ challenges {d.get('claim_id')}: {clip(d.get('reason'), 50)}"
    if k == ev.CLAIM_CORROBORATED:
        return f"{a} ≡ corroborates {d.get('claim_id')} via {_domain(d.get('source', ''))}"
    if k == ev.MESSAGE_SENT:
        return f"{a} → {d.get('to')}: {clip(d.get('text'), 60)}"
    if k == ev.PROPOSAL_POSTED:
        unit = f" {d.get('unit')}" if d.get("unit") else ""
        return f"{a} ◆ proposes {clip(d.get('value'), 50)}{unit}"
    if k == ev.VOTE_WINDOW_OPENED:
        return f"ballot opened: {', '.join(c['id'] + '=' + clip(c['value'], 20) for c in d.get('candidates', []))}"
    if k == ev.VOTE_CAST:
        return f"{a} ☐ ranks {' > '.join(d.get('ranking') or [])}"
    if k == ev.ANSWER_SUBMITTED:
        unit = f" {d.get('unit')}" if d.get("unit") else ""
        return f"{a} ● answers {clip(d.get('answer'), 50)}{unit}"
    if k == ev.AGENT_DROPPED:
        return f"{a} ✕ went dark after {d.get('after_calls')} tool call(s)"
    if k == ev.WARNING:
        return f"warning: {d.get('message')}"
    return None


def describe_round(d: dict[str, Any], round_no: int | None) -> str:
    phase = d.get("phase", "")
    head = f"r{round_no or '?'} {phase:<10}"
    decision = d.get("decision", "")
    if phase == "ballot":
        firsts = " ".join(f"{k}:{v}" for k, v in (d.get("first_choices") or {}).items())
        return f"{head} first choices {firsts}  needs {d.get('needed')}/{d.get('alive')} → {decision}"
    clusters = d.get("clusters") or []
    shown = " · ".join(f"{clip(c.get('value'), 24)} ×{c.get('support')}" for c in clusters[:4]) or "no answers"
    if len(clusters) > 4:
        shown += f" · +{len(clusters) - 4}"
    need = f"  needs {d.get('needed')}/{d.get('alive')}" if d.get("needed") and d.get("alive") else ""
    return f"{head} {shown}{need} → {decision}"


class LiveState:
    def __init__(self, theme: Theme, run_dir: Path | None = None) -> None:
        self.theme = theme
        self.run_dir = run_dir
        self.info: dict[str, Any] = {}
        self.agents: dict[str, dict[str, Any]] = {}
        self.ledger: deque[str] = deque(maxlen=14)
        self.rounds: list[str] = []
        self.round: int | None = None
        self.phase = ""
        self.tokens = 0
        self.cost: float | None = None
        self.final: dict[str, Any] | None = None
        self.t0 = time.time()

    def apply(self, e: Event) -> None:
        d = e.data
        ag = self.agents.get(e.agent or "")
        k = e.kind
        if k == ev.SESSION_STARTED:
            self.info = d
            self.t0 = e.t
            for a in d.get("fleet", []):
                self.agents[a["id"]] = {"label": a["label"], "role": a["role"], "status": "idle", "calls": 0,
                                        "tokens": 0, "cost": None, "last": "", "task": "", "phase": "",
                                        "since": None, "recent": deque(maxlen=RECENT), "live_usage": False}
        elif k == ev.ROUND_STARTED:
            self.round, self.phase = e.round, d.get("phase", "")
        elif ag is None:
            pass
        elif k == ev.TURN_STARTED:
            ag.update(status="thinking", last=d.get("phase", ""), phase=d.get("phase", ""), since=e.t)
        elif k == ev.TOOL_CALLED:
            tool = d.get("tool", "")
            ag.update(status="tool", last=tool)
            ag["calls"] += 1
            ag["recent"].append(["⋯", f"{tool} {tool_brief(tool, d.get('full'), d.get('args'))}", "run"])
        elif k == ev.TOOL_RETURNED:
            if ag["status"] == "tool":
                ag["status"] = "thinking"
            for step in reversed(ag["recent"]):
                if step[2] == "run":
                    ok = bool(d.get("ok"))
                    step[0], step[2] = ("✓", "ok") if ok else ("✗", "err")
                    if not ok and d.get("error"):
                        step[1] += f"  — {clip(d['error'], 60)}"
                    break
            if not d.get("ok") and d.get("error"):
                ag["last"] = f"{d.get('tool')} ✗"
        elif k == ev.USAGE:
            ag["live_usage"] = True
            ag["tokens"] = (d.get("input_tokens") or 0) + (d.get("output_tokens") or 0)
            ag["cost"] = d.get("cost_usd")
            self.cost = d.get("run_cost_usd", self.cost)
        elif k == ev.TURN_FINISHED:
            status = d.get("status", "done")
            if ag["status"] != "dropped":
                ag["status"] = "done" if status == "finished" else status
            if not ag["live_usage"]:  # older run logs carry tokens only here
                ag["tokens"] += (d.get("input_tokens") or 0) + (d.get("output_tokens") or 0)
            if d.get("cost_usd") is not None:
                ag["cost"] = d["cost_usd"]
            for step in ag["recent"]:
                if step[2] == "run":
                    step[0], step[2] = "·", "muted"
            if d.get("error"):
                ag["last"] = clip(d["error"], 40)
                ag["recent"].append(["!", clip(d["error"], 90), "err"])
            ag["since"] = None
        elif k == ev.MESSAGE_SENT:
            pass  # shown through the send_message tool call
        elif k == ev.BUDGET_REACHED:
            ag["recent"].append(["$", f"budget reached: {_fmt_cost(d.get('cost_usd'))} of ${d.get('limit'):g}",
                                 "err"])
        elif k == ev.AGENT_DROPPED:
            ag.update(status="dropped", last="went dark")
            ag["recent"].append(["✕", f"went dark after {d.get('after_calls')} tool call(s)", "err"])
        if k == ev.ROUND_CLOSED:
            self.rounds.append(describe_round(d, e.round))
        elif k == ev.SESSION_FINISHED:
            self.final = d
            if d.get("total_cost_usd") is not None:
                self.cost = d["total_cost_usd"]
        self.tokens = sum(a["tokens"] for a in self.agents.values())
        line = describe_event(e)
        if line:
            self.ledger.append(line)

    # -- rendering ----------------------------------------------------------

    def header_panel(self) -> Panel:
        t = self.theme
        info = self.info
        tools = info.get("tools") or {}
        flags = [f"web {'on' if tools.get('web') else 'off'}"]
        flags.append(f"python {tools.get('sandbox')}" if tools.get("python") else "python off")
        flags += info.get("features") or []
        elapsed = (self.final or {}).get("duration_s") or (time.time() - self.t0)
        where = f"round {self.round} · {self.phase}" if self.round else "starting"
        body = Text()
        body.append("Q ", style=t.accent)
        body.append(clip(info.get("question", ""), 300) + "\n")
        body.append("fleet ", style=t.muted)
        labels: dict[str, int] = {}
        for a in info.get("fleet", []):
            labels[a["label"]] = labels.get(a["label"], 0) + 1
        body.append(", ".join(f"{k} ×{v}" for k, v in labels.items()) + "\n")
        body.append(" · ".join(flags), style=t.muted)
        opts = info.get("options") or {}
        if opts:
            body.append("  ·  " + " ".join(f"{k}={v}" for k, v in opts.items()), style=t.muted)
        title = Text.assemble(("bannaha", t.title), " · ", (info.get("strategy", ""), t.accent))
        cost = f" · {_fmt_cost(self.cost)}" if self.cost is not None else ""
        subtitle = f"{where} · {elapsed:,.0f}s · {_fmt_tokens(self.tokens)} tokens{cost}"
        return Panel(body, title=title, title_align="left", subtitle=subtitle, subtitle_align="right",
                     border_style=t.border)

    def agents_panel(self) -> Panel:
        t = self.theme
        table = Table(expand=True, box=None, pad_edge=False, show_edge=False)
        table.add_column("id", style=t.agent, no_wrap=True)
        table.add_column("model", style=t.muted, overflow="ellipsis", no_wrap=True, ratio=3)
        table.add_column("status", no_wrap=True)
        table.add_column("calls", justify="right")
        table.add_column("tok", justify="right", style=t.muted)
        table.add_column("cost", justify="right", style=t.muted)
        table.add_column("last", style=t.tool, overflow="ellipsis", no_wrap=True, ratio=2)
        for aid, a in self.agents.items():
            icon, style_key = STATUS.get(a["status"], ("·", "muted"))
            style = getattr(t, style_key)
            role = "" if a["role"] == "peer" else f" {a['role']}"
            table.add_row(f"{aid}{role}", a["label"], Text(f"{icon} {a['status']}", style=style),
                          str(a["calls"]), _fmt_tokens(a["tokens"]),
                          _fmt_cost(a["cost"]) if a["cost"] is not None else "", a["last"])
        return Panel(table, title="agents", title_align="left", border_style=t.border)

    def activity_panel(self) -> Panel:
        """Per agent: what it was given, what it is doing now, and its last few steps."""
        t = self.theme
        now = time.time()
        body = Text(no_wrap=True, overflow="ellipsis")
        spin = SPINNER[int(now * 10) % len(SPINNER)]
        finished = self.final is not None
        for i, (aid, a) in enumerate(self.agents.items()):
            if i:
                body.append("\n")
            busy = a["status"] in ("thinking", "tool") and not finished
            icon, style_key = STATUS.get(a["status"], ("·", "muted"))
            body.append(f"{spin if busy else icon} ", getattr(t, style_key))
            body.append(aid, f"bold {t.agent}")
            if a["role"] != "peer":
                body.append(f" {a['role']}", t.accent)
            body.append(f"  {a['label'].split(':', 1)[-1]}", t.muted)
            task = a["task"] or a["phase"]
            if task:
                body.append("  — ", t.muted)
                body.append(clip(task, 60), "bold")
            stats = [f"{a['calls']} call{'s' if a['calls'] != 1 else ''}"] if a["calls"] else []
            if a["tokens"]:
                stats.append(f"{_fmt_tokens(a['tokens'])} tok")
            if a["cost"] is not None and a["tokens"]:
                stats.append(_fmt_cost(a["cost"]))
            if busy and a["since"]:
                stats.append(f"{now - a['since']:.0f}s")
            if a["status"] not in ("idle", "thinking", "tool"):
                stats.append(a["status"])
            if stats:
                body.append("   " + " · ".join(stats), t.muted)
            if not a["recent"] and a["status"] == "idle":
                body.append("\n  ⎿ ", t.muted)
                body.append("waiting" if not finished else "not used in this run", t.muted)
            for icon2, text, kind in a["recent"]:
                body.append("\n  ⎿ ", t.muted)
                style = {"ok": t.ok, "err": t.err, "run": t.tool}.get(kind, t.muted)
                body.append(f"{spin if kind == 'run' and not finished else icon2} ", style)
                body.append(text, t.tool if kind == "run" else (t.err if kind == "err" else ""))
        return Panel(body if self.agents else Text("no agents yet", style=t.muted), title="activity",
                     title_align="left", border_style=t.border)

    def ledger_panel(self) -> Panel:
        t = self.theme
        text = Text("\n".join(self.ledger) if self.ledger else "waiting for activity…",
                    style=t.claim if self.ledger else t.muted, overflow="ellipsis", no_wrap=True)
        return Panel(text, title="board", title_align="left", border_style=t.border)

    def rounds_panel(self) -> Panel:
        t = self.theme
        text = Text("\n".join(self.rounds[-6:]) if self.rounds else "no round closed yet",
                    style="" if self.rounds else t.muted)
        return Panel(text, title="rounds", title_align="left", border_style=t.border)

    def final_panel(self) -> Panel | None:
        if not self.final:
            return None
        t = self.theme
        o = self.final.get("outcome") or {}
        icon, key = OUTCOME.get(o.get("status", ""), ("?", "warn"))
        style = getattr(t, key)
        body = Text()
        body.append(f"{icon} {o.get('status')}  ", style=style)
        if o.get("answer") is not None:
            body.append(str(o.get("answer")), style="bold")
            if o.get("unit"):
                body.append(f" {o['unit']}")
        detail = o.get("detail") or {}
        bits = [str(detail[k]) for k in ("decided_by",) if detail.get(k)]
        if detail.get("reason"):
            bits.append(str(detail["reason"]))
        if "agreement" in detail:
            bits.append(f"agreement {detail['agreement']:.0%}")
        if bits:
            body.append("   " + " · ".join(bits), style=t.muted)
        if self.run_dir:
            body.append(f"\nrun saved to {tilde(self.run_dir)}", style=t.muted)
        return Panel(body, title="answer", title_align="left", border_style=style)

    def render(self) -> Group:
        grid = Table.grid(expand=True)
        grid.add_column(ratio=11)
        grid.add_column(ratio=9)
        grid.add_row(self.agents_panel(), self.ledger_panel())
        parts: list[Any] = [self.header_panel(), grid, self.activity_panel(), self.rounds_panel()]
        final = self.final_panel()
        if final is not None:
            parts.append(final)
        return Group(*parts)


def _run_in_thread(session: Session, on_tick) -> Outcome | None:
    box: dict[str, Outcome] = {}
    th = threading.Thread(target=lambda: box.setdefault("o", session.run()), daemon=True, name="bannaha-session")
    th.start()
    try:
        while th.is_alive():
            th.join(0.1)
            on_tick()
    except KeyboardInterrupt:
        session.cancel("cancelled by user")
        deadline = time.time() + 15
        while th.is_alive() and time.time() < deadline:
            th.join(0.2)
            on_tick()
    on_tick()
    return box.get("o")


def run_live(session: Session, console: Console, theme: Theme, run_dir: Path | None) -> Outcome | None:
    state = LiveState(theme, run_dir)
    q: queue.Queue[Event] = queue.Queue()
    session.bus.subscribe(q.put)
    with Live(state.render(), console=console, refresh_per_second=8, transient=False) as live:
        def tick() -> None:
            changed = False
            while True:
                try:
                    state.apply(q.get_nowait())
                    changed = True
                except queue.Empty:
                    break
            if changed or state.final is None:
                live.update(state.render())

        return _run_in_thread(session, tick)


def run_plain(session: Session, console: Console, theme: Theme, run_dir: Path | None) -> Outcome | None:
    def printer(e: Event) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime(e.t))
        if e.kind == ev.SESSION_STARTED:
            console.print(f"[{theme.muted}]{stamp}[/] [{theme.accent}]{e.data.get('strategy')}[/] "
                          f"{len(e.data.get('fleet', []))} agents · {escape(clip(e.data.get('question'), 120))}")
        elif e.kind == ev.ROUND_STARTED:
            console.print(f"[{theme.muted}]{stamp}[/] round {e.round} · {e.data.get('phase')}")
        elif e.kind == ev.ROUND_CLOSED:
            console.print(f"[{theme.muted}]{stamp}[/] {escape(describe_round(e.data, e.round))}")
        elif e.kind == ev.TOOL_CALLED:
            tool = e.data.get("tool")
            console.print(f"[{theme.muted}]{stamp}[/] [{theme.agent}]{e.agent}[/] [{theme.tool}]{tool}[/] "
                          f"[{theme.muted}]{escape(clip(e.data.get('args'), 90))}[/]", highlight=False)
        elif e.kind == ev.TURN_FINISHED and e.data.get("status") != "finished":
            console.print(f"[{theme.muted}]{stamp}[/] [{theme.agent}]{e.agent}[/] turn ended: {e.data.get('status')} "
                          f"{escape(e.data.get('error') or '')}")
        elif e.kind == ev.SESSION_FINISHED:
            o = e.data.get("outcome") or {}
            console.print(f"[{theme.muted}]{stamp}[/] [bold]{o.get('status')}[/] {escape(str(o.get('answer') or ''))} "
                          f"{escape(o.get('unit') or '')} ({e.data.get('duration_s')}s)")
            if run_dir:
                console.print(f"[{theme.muted}]run saved to {escape(str(run_dir))}[/]")
        else:
            line = describe_event(e)
            if line:
                console.print(f"[{theme.muted}]{stamp}[/] {escape(line)}", highlight=False)

    session.bus.subscribe(printer)
    return _run_in_thread(session, lambda: None)
