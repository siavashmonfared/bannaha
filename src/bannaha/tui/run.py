"""Page 4: ask a question and watch the fleet work. Enter runs, Esc cancels."""
from __future__ import annotations

import queue
from typing import TYPE_CHECKING, ClassVar

from rich.panel import Panel
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, RichLog, Static
from textual.worker import Worker, WorkerState

from ..cli.live import LiveState
from ..cli.runner import limits_for, prepare
from ..cli.themes import get_theme
from ..cli.transcript import Transcript, load_events
from ..core.errors import BannahaError
from ..core.events import Event
from ..followup import FollowUp, RunMemory, help_renderable, parse_command, rerun_question
from ..tools.toolbox import ToolOptions
from .banner import Banner
from .widgets import PageBar

if TYPE_CHECKING:
    from ..core.session import Session
    from .app import BannahaApp


class RunScreen(Screen):
    BINDINGS: ClassVar = [
        Binding("escape", "escape", "cancel run / leave input"),
        Binding("i", "focus_input", "ask"),
        Binding("q", "app.quit", "quit"),
        Binding("ctrl+o", "toggle_full", "expand", priority=True),
        Binding("ctrl+g", "cycle_focus", "follow agent", priority=True),
        Binding("ctrl+t", "toggle_dashboard", "dashboard", priority=True),
    ]
    DEFAULT_CSS = """
    RunScreen #log { height: 1fr; padding: 0 2; scrollbar-size-vertical: 1; }
    RunScreen #live { height: 1fr; padding: 0 1; display: none; }
    RunScreen #now { height: 1; padding: 0 2; }
    RunScreen #mid { height: auto; }
    RunScreen #agents { width: 11fr; }
    RunScreen #board { width: 9fr; }
    RunScreen #ask { margin: 0 1; border: round $accent; }
    RunScreen #statusline { height: 1; padding: 0 2; color: $text-muted; }
    """

    @property
    def bapp(self) -> BannahaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield PageBar("run")
        yield Banner()
        yield RichLog(id="log", wrap=True, auto_scroll=True, markup=False, highlight=False, min_width=40)
        with VerticalScroll(id="live"):
            yield Static(id="hdr")
            with Horizontal(id="mid"):
                yield Static(id="agents")
                yield Static(id="board")
            yield Static(id="activity")
            yield Static(id="rounds")
            yield Static(id="answer")
        yield Static(id="now")
        yield Input(placeholder="› Ask the fleet a question and press Enter", id="ask")
        yield Static(id="statusline")
        yield Footer()

    def on_mount(self) -> None:
        self.state: LiveState | None = None
        self.session: Session | None = None
        self.events: queue.Queue[Event] = queue.Queue()
        self.history: list[str] = []
        self.hidx: int | None = None
        self.seen: list[Event] = []
        self.tr: Transcript | None = None
        self.full = False
        self.focus: str | None = None
        self.chat: FollowUp | None = None       # set once a run has finished
        self.mode = "run"                       # what the current worker is: "run" or "followup"
        self.fu_done: list[tuple[str, list[Event]]] = []  # finished follow-ups, for re-rendering
        self.fu_events: list[Event] = []
        self.fu_question = ""
        self.fu_tr: Transcript | None = None
        self.set_interval(1 / 8, self.tick)
        self.app.begin_capture_print(self, stdout=True, stderr=True)
        self.show_idle()

    def on_screen_resume(self) -> None:
        if self.session is None and self.chat is None:
            self.show_idle()
        self.query_one("#ask", Input).focus()

    # -- display ------------------------------------------------------------

    def _status(self, extra: str = "") -> None:
        keys = "Enter run · ↑↓ history · ctrl+o expand · ctrl+g follow agent · ctrl+t dashboard · Esc cancel"
        self.query_one("#statusline", Static).update(Text(f"{self.bapp.summary()}   {extra or keys}"))

    def show_idle(self) -> None:
        if self.state is not None:
            self.render_state()
            self._status()
            return
        problems = self.bapp.problems()
        body = Text()
        body.append(f"{self.bapp.summary()}\n\n", "bold")
        if problems:
            body.append("Not ready to run:\n", "bold red")
            for p in problems:
                body.append(f"  • {p}\n", "red")
            body.append("\nFix it on the Setup (1), Fleet (2) or Keys (3) page.", "dim")
        else:
            body.append("Type a question below and press Enter. The live view of agents, board activity, rounds "
                        "and the answer appears here.", "dim")
        self.query_one("#hdr", Static).update(Panel(body, title="bannaha", title_align="left",
                                                    border_style="cyan"))
        log = self.query_one("#log", RichLog)
        log.clear()
        log.write(body)
        for wid in ("#agents", "#board", "#activity", "#rounds", "#answer"):
            self.query_one(wid, Static).update("")
        self._status()

    def render_state(self) -> None:
        st = self.state
        if st is None:
            return
        self.query_one("#hdr", Static).update(st.header_panel())
        self.query_one("#agents", Static).update(st.agents_panel())
        self.query_one("#board", Static).update(st.ledger_panel())
        self.query_one("#activity", Static).update(st.activity_panel())
        self.query_one("#rounds", Static).update(st.rounds_panel())
        final = st.final_panel()
        self.query_one("#answer", Static).update(final if final is not None else "")

    def tick(self) -> None:
        if self.state is None:
            return
        changed = False
        log = self.query_one("#log", RichLog)
        while True:
            try:
                e = self.events.get_nowait()
            except queue.Empty:
                break
            if self.mode == "followup" and self.fu_tr is not None:
                self.fu_events.append(e)
                for block in self.fu_tr.feed(e):
                    log.write(block, expand=True)
                continue
            self.state.apply(e)
            self.seen.append(e)
            if self.tr is not None:
                for block in self.tr.feed(e):
                    log.write(block, expand=True)
            changed = True
        current = self.fu_tr if self.mode == "followup" and self.fu_tr is not None else self.tr
        if current is not None:
            self.query_one("#now", Static).update(current.status(self.state.run_dir))
        if (changed or self.session is not None) and self.query_one("#live").display:
            self.render_state()

    def _rerender(self) -> None:
        if self.state is None:
            return
        log = self.query_one("#log", RichLog)
        log.clear()
        self.tr = Transcript(get_theme(self.bapp.settings.theme), full=self.full, focus=self.focus)
        for e in self.seen:
            for block in self.tr.feed(e):
                log.write(block, expand=True)
        for question, evs in self.fu_done:
            self._write_you(question)
            ftr = Transcript(get_theme(self.bapp.settings.theme), full=self.full, followup=True, colors=self.tr.agents)
            for e in evs:
                for block in ftr.feed(e):
                    log.write(block, expand=True)
        self.query_one("#now", Static).update(self.tr.status(self.state.run_dir))

    def on_resize(self, event) -> None:
        if self.seen:
            self.call_after_refresh(self._rerender)  # blocks are laid out at write time; redo them at the new width

    def action_toggle_full(self) -> None:
        self.full = not self.full
        self._rerender()
        self.notify("tool results in full" if self.full else "tool results shortened")

    def action_cycle_focus(self) -> None:
        ids = [a for a in (self.tr.agents if self.tr else {})]
        if not ids:
            return
        order = [None, *ids]
        self.focus = order[(order.index(self.focus) + 1) % len(order)] if self.focus in order else None
        self._rerender()
        self.notify(f"following {self.focus}" if self.focus else "showing all agents")

    def action_toggle_dashboard(self) -> None:
        live, log = self.query_one("#live"), self.query_one("#log", RichLog)
        live.display, log.display = not live.display, not log.display
        if live.display:
            self.render_state()

    def on_print(self, event: events.Print) -> None:
        text = event.text.strip()
        if text and self.state is not None:
            self.state.ledger.append(f"· {text[:160]}")
            self.query_one("#log", RichLog).write(Text(f"  · {text}", "dim"))

    # -- running ------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self.session is not None:
            self.notify("busy; Esc stops the current task", severity="warning")
            return
        ask = self.query_one("#ask", Input)
        ask.value = ""
        self.history.append(text)
        self.hidx = None
        cmd, arg = parse_command(text)
        log = self.query_one("#log", RichLog)
        if cmd == "/help":
            log.write(Text(""))
            log.write(help_renderable(get_theme(self.bapp.settings.theme)), expand=True)
        elif cmd == "/new":
            if not arg:
                self.notify("usage: /new <task>", severity="warning")
            else:
                self._start_run(arg)
        elif cmd == "/rerun":
            if self.chat is None:
                self.notify("/rerun needs a finished run first", severity="warning")
            elif not arg:
                self.notify("usage: /rerun <clarification>", severity="warning")
            else:
                self._start_run(rerun_question(self.chat.memory, arg))
        elif cmd == "/to":
            if self.chat is None:
                self.notify("/to needs a finished run first", severity="warning")
                return
            try:
                label = self.chat.set_agent(arg)
            except BannahaError as exc:
                self.notify(str(exc), severity="error")
                return
            log.write(Text(f"  follow-ups now go to {self.chat.agent_id()} · {label}", "dim"))
            self._placeholder()
        elif cmd == "/agents":
            self._write_agents()
        elif cmd == "/forget":
            if self.chat is not None:
                self.chat.forget()
                log.write(Text("  follow-up conversation cleared (the run's memory stays)", "dim"))
        elif cmd is not None:
            self.notify(f"unknown command {cmd}; /help lists them", severity="warning")
        elif self.chat is not None:
            self._start_followup(text)
        else:
            self._start_run(text)

    def _start_run(self, question: str) -> None:
        prepared, problems = prepare(question, self.bapp.settings)
        if prepared is None:
            body = Text("cannot start:\n", "bold red")
            for p in problems:
                body.append(f"  • {p}\n", "red")
            self.query_one("#log", RichLog).write(body)
            return
        self.events = queue.Queue()
        self.session = prepared.session
        self.mode = "run"
        self.chat, self.fu_done = None, []
        self.state = LiveState(get_theme(self.bapp.settings.theme), prepared.run_dir)
        self.seen = []
        self.tr = Transcript(get_theme(self.bapp.settings.theme), full=self.full, focus=self.focus)
        self.query_one("#log", RichLog).clear()
        prepared.session.bus.subscribe(self.events.put)
        ask = self.query_one("#ask", Input)
        ask.disabled = True
        self._status("running · Esc cancels")
        self.run_worker(prepared.session.run, thread=True, exclusive=True, group="run", exit_on_error=False)

    def _start_followup(self, question: str) -> None:
        s = self.bapp.settings
        opts = ToolOptions(web=s.web, python=s.python, sandbox=s.sandbox, search_model=s.search_model)
        try:
            session = self.chat.session(question, opts, limits_for(s))
        except BannahaError as exc:
            self.notify(str(exc), severity="error")
            return
        self._write_you(question)
        self.mode, self.fu_question, self.fu_events = "followup", question, []
        self.fu_tr = Transcript(get_theme(s.theme), full=self.full, followup=True,
                                colors=self.tr.agents if self.tr else None)
        self.events = queue.Queue()
        session.bus.subscribe(self.events.put)
        self.session = session
        self.query_one("#ask", Input).disabled = True
        self._status(f"asking {self.chat.agent_id()} · Esc cancels")
        self.run_worker(session.run, thread=True, exclusive=True, group="followup", exit_on_error=False)

    def _write_you(self, question: str) -> None:
        log = self.query_one("#log", RichLog)
        t = Text("\n› ", "bold")
        t.append(question, "bold")
        log.write(t, expand=True)

    def _write_agents(self) -> None:
        log = self.query_one("#log", RichLog)
        if self.chat is None:
            log.write(Text("  no finished run yet", "dim"))
            return
        out = Text()
        for a in self.chat.memory.fleet:
            mark = "  ← answers follow-ups" if a["label"] == self.chat.agent else ""
            out.append(f"  {a['id']}  {a['label']}{mark}\n")
        if self.chat.agent_id() == "ask":
            out.append(f"  ask  {self.chat.agent}  ← answers follow-ups\n")
        log.write(out)

    def _placeholder(self) -> None:
        ask = self.query_one("#ask", Input)
        if self.chat is None:
            ask.placeholder = "› Ask the fleet a question and press Enter · /help"
        else:
            ask.placeholder = (f"› Ask {self.chat.agent_id()} about this answer · /new <task> · /rerun "
                               "<clarification> · /help")

    def _after_run(self, run_dir) -> None:
        """A run just finished: its trace becomes the memory for follow-ups."""
        try:
            self.chat = FollowUp(RunMemory.from_events(self.seen, run_dir))
        except Exception as exc:  # follow-ups are a convenience; the run itself is done
            self.notify(f"follow-ups unavailable: {exc}", severity="warning")
            return
        note = Text("\n  Ask about the answer: follow-ups go to ", "dim")
        note.append(f"{self.chat.agent_id()} {self.chat.agent}", "bold")
        note.append(" (the strongest model in the fleet). /help shows all commands.", "dim")
        self.query_one("#log", RichLog).write(note, expand=True)
        self._placeholder()

    def load_run(self, run_dir) -> None:
        """Open a saved run here, with its follow-ups, ready for more questions."""
        events = load_events(run_dir / "events.jsonl")
        if not events:
            self.notify("that run has no event log", severity="error")
            return
        self.state = LiveState(get_theme(self.bapp.settings.theme), run_dir)
        for e in events:
            self.state.apply(e)
        self.seen, self.fu_done, self.mode = events, [], "run"
        prior = load_events(run_dir / "followup_events.jsonl")
        chunk: list[Event] = []
        for e in prior:
            if e.kind == "session_started" and chunk:
                self.fu_done.append((chunk[0].data.get("question", ""), chunk))
                chunk = []
            chunk.append(e)
        if chunk:
            self.fu_done.append((chunk[0].data.get("question", ""), chunk))
        self.chat = FollowUp(RunMemory.from_events(events, run_dir))
        self._rerender()
        self._after_run(run_dir)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group not in ("run", "followup") or event.state not in (
                WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            return
        self.tick()
        if event.state == WorkerState.ERROR:
            self.notify(f"{event.worker.group} failed: {event.worker.error}", severity="error")
        session, self.session = self.session, None
        ask = self.query_one("#ask", Input)
        ask.disabled = False
        ask.focus()
        run_dir = self.state.run_dir if self.state else None
        if event.worker.group == "followup":
            outcome = event.worker.result if event.state == WorkerState.SUCCESS else None
            if self.chat is not None:
                self.chat.record(self.fu_question, outcome, session.total_cost() if session else None)
            self.fu_done.append((self.fu_question, self.fu_events))
            self.mode = "run"
            self._status("answered · ask more, /new, /rerun, /help")
            return
        self._status("done · saved (see 5 Runs)" if run_dir else "done")
        if event.state == WorkerState.SUCCESS:
            self._after_run(run_dir)

    def action_escape(self) -> None:
        if self.session is not None:
            self.session.cancel("cancelled by user")
            self.notify("cancelling: agents stop at their next tool call")
        else:
            self.set_focus(None)

    def action_focus_input(self) -> None:
        self.query_one("#ask", Input).focus()

    def on_key(self, event: events.Key) -> None:
        ask = self.query_one("#ask", Input)
        if self.focused is not ask or event.key not in ("up", "down") or not self.history:
            return
        if event.key == "up":
            self.hidx = len(self.history) - 1 if self.hidx is None else max(0, self.hidx - 1)
        else:
            self.hidx = None if self.hidx is None or self.hidx >= len(self.history) - 1 else self.hidx + 1
        ask.value = self.history[self.hidx] if self.hidx is not None else ""
        ask.cursor_position = len(ask.value)
        event.stop()
