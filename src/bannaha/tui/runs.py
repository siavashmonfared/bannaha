"""Page 5: past runs. ↵ replays a run's rounds, board and answer from its event log."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, RichLog, Static

from ..cli.live import LiveState
from ..cli.themes import get_theme
from ..cli.transcript import Transcript, load_events
from ..core.events import Event
from ..core.runlog import default_runs_dir
from ..core.text import clip, tilde
from .banner import Banner
from .widgets import PageBar

if TYPE_CHECKING:
    from .app import BannahaApp

STATUS_STYLE = {"committed": "green", "failed": "red", "error": "red", "no_answer": "yellow", "stopped": "yellow"}


def list_runs(root: Path, limit: int = 300) -> list[tuple[Path, dict]]:
    if not root.is_dir():
        return []
    out = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)[:limit]:
        result = d / "result.json"
        try:
            data = json.loads(result.read_text()) if result.is_file() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        out.append((d, data))
    return out


class RunsScreen(Screen):
    BINDINGS: ClassVar = [Binding("r", "refresh", "refresh"), Binding("q", "app.quit", "quit")]
    DEFAULT_CSS = """
    RunsScreen DataTable { margin: 1 2 0 2; height: 1fr; }
    RunsScreen #info { margin: 0 2; height: 2; color: $text-muted; }
    """

    @property
    def bapp(self) -> BannahaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield PageBar("runs")
        yield Banner()
        yield DataTable(id="runs", cursor_type="row", zebra_stripes=True)
        yield Static(id="info")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("when", "strategy", "agents", "status", "answer", "time", "question")
        self.paths: list[Path] = []
        self.action_refresh()

    def on_screen_resume(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        root = default_runs_dir()
        self.paths = []
        for d, data in list_runs(root):
            outcome = data.get("outcome") or {}
            status = outcome.get("status") or "incomplete"
            stamp = d.name[:15]
            when = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}" if len(stamp) == 15 else d.name
            answer = clip(f"{outcome.get('answer') or ''} {outcome.get('unit') or ''}".strip(), 30)
            dur = f"{data['duration_s']:.0f}s" if isinstance(data.get("duration_s"), (int, float)) else ""
            table.add_row(when, data.get("strategy", "?"), str(len(data.get("fleet") or [])),
                          Text(status, STATUS_STYLE.get(status, "dim")), answer, dur,
                          clip(data.get("question", ""), 60))
            self.paths.append(d)
        info = f"{len(self.paths)} run(s) in {tilde(root)}" if self.paths else f"no runs yet in {tilde(root)}"
        self.query_one("#info", Static).update(info + "   ·   ↵ open · r refresh · 1–5 pages")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if 0 <= event.cursor_row < len(self.paths):
            self.app.push_screen(RunDetailScreen(self.paths[event.cursor_row], self.bapp.settings.theme))


class RunDetailScreen(Screen):
    BINDINGS: ClassVar = [Binding("escape", "app.pop_screen", "back"), Binding("q", "app.pop_screen", "back"),
                          Binding("ctrl+o", "toggle_full", "expand"), Binding("ctrl+g", "cycle_focus", "follow agent"),
                          Binding("ctrl+t", "toggle_dashboard", "dashboard"),
                          Binding("a", "ask_about", "ask about this run")]
    DEFAULT_CSS = """
    RunDetailScreen VerticalScroll { padding: 0 1; display: none; }
    RunDetailScreen #log { height: 1fr; padding: 0 2; }
    """

    def __init__(self, run_dir: Path, theme: str) -> None:
        super().__init__()
        self.run_dir, self.theme_name = run_dir, theme

    def compose(self) -> ComposeResult:
        yield PageBar("runs")
        yield RichLog(id="log", wrap=True, auto_scroll=False, markup=False, highlight=False, min_width=40)
        with VerticalScroll():
            yield Static(id="replay")
        yield Footer()

    def _render_log(self) -> None:
        log = self.query_one("#log", RichLog)
        log.clear()
        self.tr = Transcript(get_theme(self.theme_name), full=self.full, focus=self.focus)
        for e in self.events:
            for block in self.tr.feed(e):
                log.write(block, expand=True)
        log.scroll_home(animate=False)

    def on_resize(self, event) -> None:
        self.call_after_refresh(self._render_log)

    def action_ask_about(self) -> None:
        """Open this run on the Run page with its memory, ready for follow-up questions."""
        run_dir = self.run_dir
        app = self.app
        app.pop_screen()
        app.switch_mode("run")
        app.call_after_refresh(lambda: app.screen.load_run(run_dir))

    def action_toggle_full(self) -> None:
        self.full = not self.full
        self._render_log()

    def action_cycle_focus(self) -> None:
        order = [None, *self.tr.agents]
        self.focus = order[(order.index(self.focus) + 1) % len(order)] if self.focus in order else None
        self._render_log()
        self.notify(f"following {self.focus}" if self.focus else "showing all agents")

    def action_toggle_dashboard(self) -> None:
        dash, log = self.query_one(VerticalScroll), self.query_one("#log", RichLog)
        show = not dash.display
        dash.display, log.display = show, not show

    def on_mount(self) -> None:
        self.full, self.focus = False, None
        self.events = load_events(self.run_dir / "events.jsonl")
        self.tr = Transcript(get_theme(self.theme_name))
        self.call_after_refresh(self._render_log)  # once the log knows its width
        state = LiveState(get_theme(self.theme_name), self.run_dir)
        state.ledger = type(state.ledger)(maxlen=400)
        log = self.run_dir / "events.jsonl"
        if log.is_file():
            for line in log.read_text().splitlines():
                try:
                    d = json.loads(line)
                    state.apply(Event(kind=d["kind"], agent=d.get("agent"), round=d.get("round"),
                                      data=d.get("data") or {}, t=d.get("t") or 0.0))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
        self.query_one("#replay", Static).update(state.render())
