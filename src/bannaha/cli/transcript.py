"""A run as one continuous transcript, the way Claude Code and Codex show work.

Events become blocks in time order: a header when the speaking agent changes, one block per finished
tool call (the call and a short preview of its result), finishing tools (answers, proposals, votes,
replies) in full, each model's own text, and round boundaries. A status line shows what
is running now. Nothing is cropped into boxes: blocks wrap to the terminal width.

The same renderer feeds the inline stream (`bannaha run`, `bannaha ask`) and the scrollable
transcript in the full-screen app; `full=True` expands tool results, `focus` follows one agent.
"""
from __future__ import annotations

import json
import time
from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ..core import events as ev
from ..core.events import Event
from ..core.text import clip, tilde
from .live import SPINNER, _fmt_cost, _fmt_tokens, describe_event, describe_round, tool_brief
from .themes import Theme

PALETTE = ["dodger_blue1", "orange1", "medium_spring_green", "orchid", "gold1", "deep_sky_blue1", "salmon1",
           "light_green", "violet", "turquoise2", "khaki1", "hot_pink", "sky_blue1", "pale_green1"]
FINISHING = {
    "final_answer": "Answer", "propose_answer": "Proposal", "cast_vote": "Vote", "send_message": "Message",
    "reply": "Reply",
}
FIELD_ORDER = ("answer", "value", "unit", "confidence", "text", "to", "window_id", "ranking", "rationale", "reason",
               "claim_ids", "sources")
SKIP_FIELDS = {"key_facts"}
STATUS_ICON = {"committed": "✔", "no_answer": "∅", "failed": "✘", "stopped": "■", "error": "!"}


class Transcript:
    def __init__(self, theme: Theme, *, full: bool = False, focus: str | None = None, preview_lines: int = 4,
                 followup: bool = False, colors: dict[str, dict[str, Any]] | None = None) -> None:
        self.t = theme
        self.followup = followup  # a follow-up turn: no run header, no round rules, a short footer
        self.colors = colors or {}
        self.full = full
        self.focus = focus
        self.preview_lines = preview_lines
        self.agents: dict[str, dict[str, Any]] = {}
        self.last: str | None = None
        self.pending: dict[str, list[dict[str, Any]]] = {}
        self.active: dict[str, dict[str, Any]] = {}
        self.round: int | None = None
        self.phase = ""
        self.t0 = time.time()
        self.tokens: dict[str, int] = {}
        self.cost: dict[str, float | None] = {}
        self.run_cost: float | None = None
        self.final: dict[str, Any] | None = None
        self.info: dict[str, Any] = {}

    # -- helpers --------------------------------------------------------------------------------------------------

    def color(self, aid: str | None) -> str:
        return self.agents.get(aid or "", {}).get("color", self.t.agent)

    def _show(self, aid: str | None) -> bool:
        return self.focus is None or aid is None or aid == self.focus

    def _header(self, aid: str) -> list[RenderableType]:
        if aid == self.last:
            return []
        self.last = aid
        a = self.agents.get(aid, {})
        head = Text()
        head.append("● ", self.color(aid))
        head.append(aid, f"bold {self.color(aid)}")
        head.append(f"  {a.get('model', '')}", self.color(aid))
        role = a.get("role")
        if role and role != "peer":
            head.append(f"  {role}", self.t.accent)
        phase = self.active.get(aid, {}).get("phase")
        if phase:
            head.append(f"  · {phase}", self.t.muted)
        return [Text(""), head]

    def _lines(self, text: str, limit: int | None) -> Text:
        lines = text.rstrip().splitlines() or [""]
        shown = lines if limit is None else lines[:limit]
        out = Text()
        for i, line in enumerate(shown):
            out.append(("⎿  " if i == 0 else "   ") + (line if self.full else clip(line, 240)) + "\n")
        if limit is not None and len(lines) > limit:
            out.append(f"   … +{len(lines) - limit} lines (ctrl+o to expand)\n", self.t.muted)
        out.rstrip()
        return out

    # -- events ---------------------------------------------------------------------------------------------------

    def feed(self, e: Event) -> list[RenderableType]:
        k, d, aid = e.kind, e.data, e.agent
        out: list[RenderableType] = []
        if k == ev.SESSION_STARTED:
            self.info, self.t0 = d, e.t
            for i, a in enumerate(d.get("fleet", [])):
                color = self.colors.get(a["id"], {}).get("color") or PALETTE[i % len(PALETTE)]
                self.agents[a["id"]] = {"label": a["label"], "model": a["label"].split(":", 1)[-1],
                                        "role": a.get("role"), "color": color}
            return [] if self.followup else [self._session_header(d)]
        if k == ev.ROUND_STARTED and self.followup:
            return []
        if k == ev.ROUND_STARTED:
            self.round, self.phase, self.last = e.round, d.get("phase", ""), None
            return [Text(""), Rule(f"round {e.round} · {d.get('phase', '')}", style=self.t.border, align="left")]
        if k == ev.TURN_STARTED and aid:
            self.active[aid] = {"phase": d.get("phase", ""), "since": e.t, "tool": None}
            return []
        if k == ev.TOOL_CALLED and aid:
            self.pending.setdefault(aid, []).append({"tool": d.get("tool"), "full": d.get("full"),
                                                     "args": d.get("args"), "t": e.t})
            if aid in self.active:
                self.active[aid].update(tool=d.get("tool"), tool_since=e.t)
            return []
        if k == ev.TOOL_RETURNED and aid:
            queue = self.pending.get(aid, [])
            call = next((c for c in queue if c["tool"] == d.get("tool")), None) or {"tool": d.get("tool"), "full": None}
            if call in queue:
                queue.remove(call)
            if aid in self.active:
                self.active[aid]["tool"] = None
            if not self._show(aid):
                return []
            out += self._header(aid)
            out.append(self._tool_block(call, d, e.t))
            return out
        if k == ev.TURN_FINISHED and aid:
            self.active.pop(aid, None)
            if d.get("cost_usd") is not None:
                self.cost[aid] = d["cost_usd"]
            if not self._show(aid):
                return []
            text = (d.get("text") or "").strip()
            if text and len(text) > 3:
                out += self._header(aid)
                body = text if self.full else "\n".join(text.splitlines()[:14])
                more = "" if self.full or len(text.splitlines()) <= 14 else \
                    f"\n\n*… +{len(text.splitlines()) - 14} lines (ctrl+o to expand)*"
                out.append(Padding(Markdown(body + more), (0, 0, 0, 2)))
            status = d.get("status")
            if status and status != "finished":
                out += self._header(aid)
                err = f": {clip(d.get('error'), 300)}" if d.get("error") else ""
                out.append(Text(f"  ⚠ turn ended {status}{err}", self.t.warn))
            return out
        if k == ev.USAGE and aid:
            self.tokens[aid] = (d.get("input_tokens") or 0) + (d.get("output_tokens") or 0)
            self.cost[aid] = d.get("cost_usd")
            self.run_cost = d.get("run_cost_usd", self.run_cost)
            return []
        if k == ev.ROUND_CLOSED:
            self.last = None
            line = Text("⇢ ", self.t.accent)
            line.append(describe_round(d, e.round), "bold")
            return [Text(""), line]
        if k == ev.SESSION_FINISHED:
            self.final = d
            if d.get("total_cost_usd") is not None:
                self.run_cost = d["total_cost_usd"]
            if self.followup:
                o = d.get("outcome") or {}
                out = []
                if o.get("status") != "committed":
                    out.append(Text(f"  ⚠ no reply ({(o.get('detail') or {}).get('status', o.get('status'))})",
                                    self.t.warn))
                cost = f" · {_fmt_cost(d['total_cost_usd'])}" if d.get("total_cost_usd") is not None else ""
                out.append(Text(f"  {d.get('duration_s', 0):.0f}s · {_fmt_tokens(d.get('total_tokens') or 0)} "
                                f"tokens{cost}", self.t.muted))
                return out
            return self._final_block(d)
        line = describe_event(e)
        if line and k not in (ev.ANSWER_SUBMITTED, ev.VOTE_CAST, ev.PROPOSAL_POSTED, ev.MESSAGE_SENT):
            if not self._show(aid):
                return []
            text = Text("  ")
            if aid:
                text.append(f"{aid} ", self.color(aid))
                line = line[len(aid):].lstrip() if line.startswith(aid) else line
            text.append(line, self.t.err if k in (ev.AGENT_DROPPED, ev.BUDGET_REACHED) else self.t.muted)
            return [text]
        return []

    # -- blocks ---------------------------------------------------------------------------------------------------

    def _session_header(self, d: dict[str, Any]) -> RenderableType:
        t = self.t
        head = Text()
        head.append("bannaha", f"bold {t.title}")
        head.append(f" · {d.get('strategy', '')}\n", t.accent)
        head.append(d.get("question", "") + "\n", "bold")
        fleet = Text()
        for aid, a in self.agents.items():
            fleet.append(f"  {aid} ", f"bold {a['color']}")
            fleet.append(f"{a['label']}", a["color"])
            if a.get("role") and a["role"] != "peer":
                fleet.append(f"  {a['role']}", t.accent)
            fleet.append("\n")
        tools = d.get("tools") or {}
        bits = [f"web {'on' if tools.get('web') else 'off'}",
                f"python {tools.get('sandbox')}" if tools.get("python") else "python off"]
        opts = d.get("options") or {}
        bits += [f"{k}={clip(v, 30)}" for k, v in opts.items() if v not in ("", None, False, 0, 0.0)]
        lim = d.get("limits") or {}
        if lim.get("cost_usd"):
            bits.append(f"run cap ${lim['cost_usd']:.2f}")
        fleet.append("  " + " · ".join(bits), t.muted)
        return Group(head, fleet)

    def _tool_block(self, call: dict[str, Any], d: dict[str, Any], t_end: float) -> RenderableType:
        tool = call.get("tool") or d.get("tool") or "?"
        ok = d.get("ok", True)
        if tool in FINISHING and ok:
            return self._message(tool, call.get("full"))
        line = Text("  ⏺ ", self.t.err if not ok else self.color(self.last))
        line.append(tool, "bold")
        brief = tool_brief(tool, call.get("full"), call.get("args"))
        if brief:
            line.append(f"  {brief}", self.t.tool)
        took = t_end - call.get("t", t_end)
        if took >= 2:
            line.append(f"  {took:.0f}s", self.t.muted)
        preview = d.get("preview") or (f"error: {d.get('error')}" if d.get("error") else "")
        if not preview:
            return line
        body = self._lines(preview, None if self.full else self.preview_lines)
        if not ok:
            body.stylize(self.t.err)
        return Group(line, Padding(body, (0, 0, 0, 4)))

    def _message(self, tool: str, full: str | None) -> RenderableType:
        try:
            args = json.loads(full or "")
        except (TypeError, ValueError):
            args = None
        title = Text("  ● ", self.color(self.last))
        title.append(FINISHING.get(tool, tool), f"bold {self.color(self.last)}")
        if not isinstance(args, dict):
            return title
        parts: list[RenderableType] = [title]
        keys = [k for k in FIELD_ORDER if k in args] + [k for k in args if k not in FIELD_ORDER]
        answer = str(args.get("answer") or "")
        facts, rest = [], []
        for key in keys:
            val = args[key]
            if key in SKIP_FIELDS or val in (None, "", [], {}):
                continue
            if key in ("value", "unit") and answer and str(val) in answer:
                continue  # already part of the answer
            short = isinstance(val, (bool, int, float)) or (isinstance(val, str) and len(val) <= 24 and "\n" not in val)
            if short and key not in ("answer", "value", "summary", "title"):
                facts.append((key, val))
            else:
                rest.append(key)
        for key in [k for k in rest if k in ("answer", "value", "summary", "title")]:
            parts.append(Padding(self._field(key, args[key]), (0, 0, 0, 4)))
        if facts:
            line = Text()
            for i, (key, val) in enumerate(facts):
                if i:
                    line.append("  ·  ", self.t.muted)
                line.append(f"{_label(key)} ", self.t.muted)
                line.append(_fmt_value(key, val), "bold")
            parts.append(Padding(line, (0, 0, 0, 4)))
        for key in [k for k in rest if k not in ("answer", "value", "summary", "title")]:
            r = self._field(key, args[key])
            if r is not None:
                parts.append(Padding(r, (0, 0, 0, 4)))
        return Group(*parts)

    def _field(self, key: str, val: Any) -> RenderableType | None:
        t = self.t
        if val in (None, "", [], {}):
            return None
        label = key.replace("_", " ")
        if isinstance(val, bool) or isinstance(val, (int, float)):
            return Text.assemble((f"{label}: ", t.muted), (str(val), "bold"))
        if isinstance(val, str):
            if key == "markdown" and not self.full:
                lines = val.splitlines()
                if len(lines) > 40:
                    val = "\n".join(lines[:40]) + f"\n\n*… +{len(lines) - 40} lines (ctrl+o, or open the file)*"
            if key in ("answer", "value", "summary") and len(val) < 200 and "\n" not in val:
                return Text(val, "bold")
            if len(val) < 120 and "\n" not in val:
                return Text.assemble((f"{label}: ", t.muted), val)
            return Group(Text(f"{label}:", t.muted), Markdown(val))
        if isinstance(val, list):
            lines = Text()
            lines.append(f"{label}:\n", t.muted)
            for item in val[: None if self.full else 12]:
                lines.append("  • " + self._item(item) + "\n")
            if not self.full and len(val) > 12:
                lines.append(f"  … +{len(val) - 12} more\n", t.muted)
            lines.rstrip()
            return lines
        try:
            return Text.assemble((f"{label}: ", t.muted), json.dumps(val, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return None

    def _item(self, item: Any) -> str:
        if isinstance(item, dict):
            if {"agent", "item"} <= set(item):
                return f"{item['agent']} → {item['item']}"
            if "id" in item and "status" in item:
                ev_ = f" ({item['evidence']})" if item.get("evidence") else ""
                return f"{item['id']}: {item['status']}{ev_}"
            if "title" in item:
                rest = "; ".join(f"{k}: {clip(v, 200 if not self.full else 2000)}" for k, v in item.items()
                                 if k != "title" and v not in ("", None))
                return f"{item['title']}" + (f" — {rest}" if rest else "")
            return "; ".join(f"{k}: {clip(v, 300 if not self.full else 3000)}" for k, v in item.items()
                             if v not in ("", None))
        return clip(item, 400 if not self.full else 4000)

    def _final_block(self, d: dict[str, Any]) -> list[RenderableType]:
        t = self.t
        o = d.get("outcome") or {}
        status = o.get("status", "")
        style = t.ok if status == "committed" else t.warn if status in ("no_answer", "stopped") else t.err
        out: list[RenderableType] = [Text(""), Rule("result", style=style, align="left")]
        head = Text(f"{STATUS_ICON.get(status, '?')} {status}  ", f"bold {style}")
        answer = str(o.get("answer") or "")
        if answer and len(answer) < 160:
            head.append(answer + (f" {o['unit']}" if o.get("unit") else ""), "bold")
            out.append(head)
        else:
            out.append(head)
            if answer:
                out.append(Padding(Markdown(answer), (0, 0, 0, 2)))
        detail = o.get("detail") or {}
        bits = [str(detail[k]) for k in ("decided_by", "reason") if detail.get(k)]
        if bits:
            out.append(Text("  " + " · ".join(bits), t.muted))
        table = Table(box=None, pad_edge=False, show_edge=False, header_style=t.muted, padding=(0, 2))
        for col in ("agent", "model", "turns", "calls", "tokens", "cost"):
            table.add_column(col, justify="right" if col in ("turns", "calls", "tokens", "cost") else "left")
        for a in d.get("agents") or []:
            table.add_row(Text(a["id"], f"bold {self.color(a['id'])}"), self.agents.get(a["id"], {}).get("model", ""),
                          str(a.get("turns", "")), str(a.get("tool_calls", "")), _fmt_tokens(a.get("tokens") or 0),
                          _fmt_cost(a.get("cost_usd")) if a.get("cost_usd") is not None else "")
        out.append(Padding(table, (1, 0, 0, 2)))
        dur = d.get("duration_s")
        foot = f"  {dur:.0f}s · {_fmt_tokens(d.get('total_tokens') or 0)} tokens"
        if d.get("total_cost_usd") is not None:
            foot += f" · {_fmt_cost(d['total_cost_usd'])}"
        out.append(Text(foot, t.muted))
        return out

    # -- status line ----------------------------------------------------------------------------------------------

    def status(self, run_dir: Any = None) -> Text:
        t = self.t
        now = time.time()
        line = Text(no_wrap=True, overflow="ellipsis")
        if self.final is not None:
            line.append("done", t.ok)
            if run_dir:
                line.append(f" · saved to {tilde(run_dir)}", t.muted)
            return line
        spin = SPINNER[int(now * 10) % len(SPINNER)]
        working = []
        for aid, a in self.active.items():
            what = a.get("tool") or "thinking"
            since = a.get("tool_since") if a.get("tool") else a.get("since")
            working.append((aid, what, now - (since or now)))
        line.append(f"{spin} ", t.accent)
        if working:
            for i, (aid, what, secs) in enumerate(working[:6]):
                if i:
                    line.append(" · ", t.muted)
                line.append(aid, f"bold {self.color(aid)}")
                line.append(f" {what} {secs:.0f}s")
            if len(working) > 6:
                line.append(f" · +{len(working) - 6} more", t.muted)
        else:
            line.append("waiting", t.muted)
        where = f"round {self.round} · {self.phase}" if self.round else "starting"
        tok = sum(self.tokens.values())
        cost = self.run_cost if self.run_cost is not None else (sum(c for c in self.cost.values() if c) or None)
        line.append(f"   │ {where} │ {now - self.t0:,.0f}s · {_fmt_tokens(tok)} tok"
                    + (f" · {_fmt_cost(cost)}" if cost is not None else "")
                    + (f" │ following {self.focus}" if self.focus else "") + " │ esc stops", t.muted)
        return line


def _label(key: str) -> str:
    key = key.removesuffix("_usd").removesuffix("_id")
    return key.replace("est_", "est ").replace("_per_mtok", " /1M").replace("_", " ")


def _fmt_value(key: str, val: Any) -> str:
    if isinstance(val, bool):
        return "yes" if val else "no"
    if isinstance(val, (int, float)):
        if key.endswith("_usd") or key.endswith("_per_mtok"):
            return f"${val:,.4f}".rstrip("0").rstrip(".") if abs(val) < 1 else f"${val:,.2f}"
        if "tokens" in key and float(val).is_integer():
            return f"{int(val):,}"
        if key in ("p_success", "confidence") and 0 <= val <= 1:
            return f"{val:.0%}"
        return f"{val:g}"
    return str(val)


def replay(events: list[Event], theme: Theme, **kw: Any) -> tuple[Transcript, list[RenderableType]]:
    tr = Transcript(theme, **kw)
    blocks: list[RenderableType] = []
    for e in events:
        blocks += tr.feed(e)
    return tr, blocks


def load_events(path) -> list[Event]:
    out = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
            out.append(Event(kind=d["kind"], agent=d.get("agent"), round=d.get("round"), data=d.get("data") or {},
                             t=d.get("t") or 0.0))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return out


def run_stream(session, console, theme: Theme, run_dir, full: bool = False, followup: bool = False,
               colors: dict[str, dict[str, Any]] | None = None):
    """Print the run as a transcript into the terminal (it stays in scrollback), with a live status line."""
    import queue

    from rich.live import Live

    from .live import _run_in_thread

    tr = Transcript(theme, full=full, followup=followup, colors=colors)
    q: queue.Queue[Event] = queue.Queue()
    session.bus.subscribe(q.put)
    with Live(tr.status(), console=console, refresh_per_second=8, transient=True) as live:
        def tick() -> None:
            while True:
                try:
                    e = q.get_nowait()
                except queue.Empty:
                    break
                for block in tr.feed(e):
                    live.console.print(block)
            live.update(tr.status(run_dir))

        outcome = _run_in_thread(session, tick)
    if run_dir and not followup:
        console.print(Text(f"  run saved to {tilde(run_dir)}", theme.muted))
    return outcome


def fleet_colors(fleet: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {a["id"]: {"color": PALETTE[i % len(PALETTE)]} for i, a in enumerate(fleet)}
