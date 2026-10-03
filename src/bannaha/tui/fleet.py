"""Page 2: the fleet editor. One row per group of identical agents."""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ..backends import BACKENDS
from ..core.fleet import FleetEntry, parse_fleet
from ..core.registry import harnesses, strategies
from .banner import Banner
from .widgets import InputModal, PageBar, PickerModal

if TYPE_CHECKING:
    from .app import BannahaApp

CUSTOM = "__custom__"


def to_spec(entries: list[FleetEntry]) -> str:
    return ",".join(f"{e.label}*{e.count}" for e in entries)


class FleetScreen(Screen):
    BINDINGS: ClassVar = [
        Binding("a", "add", "add group"),
        Binding("e", "edit", "edit"),
        Binding("x", "delete", "remove"),
        Binding("plus,equals_sign", "count(1)", "+1"),
        Binding("minus", "count(-1)", "-1"),
        Binding("q", "app.quit", "quit"),
    ]
    DEFAULT_CSS = """
    FleetScreen DataTable { margin: 1 2 0 2; height: auto; max-height: 20; }
    FleetScreen #info { margin: 1 2; height: auto; }
    """

    @property
    def bapp(self) -> BannahaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield PageBar("fleet")
        yield Banner()
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="info")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("#", "harness", "backend", "model", "count", "status")
        self.refresh_table()

    def on_screen_resume(self) -> None:
        self.refresh_table()

    def entries(self) -> list[FleetEntry]:
        try:
            return parse_fleet(self.bapp.settings.fleet)
        except Exception:  # an unparseable saved spec starts the editor empty
            return []

    def set_entries(self, entries: list[FleetEntry]) -> None:
        self.bapp.settings.fleet = to_spec(entries) if entries else ""
        self.refresh_table()

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        registry = harnesses()
        entries = self.entries()
        for i, e in enumerate(entries):
            cls = registry.get(e.harness)
            problems = cls.check(e.backend, e.model) if cls else [f"unknown harness {e.harness!r}"]
            status = Text("✓ ready", "green") if not problems else Text(f"✗ {problems[0]}", "red")
            table.add_row(str(i + 1), e.harness, e.backend, e.model, str(e.count), status, key=str(i))
        if entries:
            table.move_cursor(row=min(row, len(entries) - 1))
        n = sum(e.count for e in entries)
        cls = strategies().get(self.bapp.settings.strategy)
        info = Text()
        info.append(f"{n} agent(s)", "bold")
        if cls is not None:
            info.append(f"  ·  strategy {cls.name} needs at least {cls.min_agents}", "dim")
        info.append("\n\na add · e edit · x remove · +/- count · 1–5 pages", "dim")
        if not entries:
            info.append("\nThe fleet is empty: press a to add a group.", "yellow")
        self.query_one("#info", Static).update(info)

    def _selected(self) -> int | None:
        entries = self.entries()
        if not entries:
            return None
        return min(self.query_one(DataTable).cursor_row, len(entries) - 1)

    def action_count(self, step: int) -> None:
        i = self._selected()
        if i is None:
            return
        entries = self.entries()
        e = entries[i]
        entries[i] = FleetEntry(e.harness, e.backend, e.model, max(1, e.count + step))
        self.set_entries(entries)

    def action_delete(self) -> None:
        i = self._selected()
        if i is None:
            return
        entries = self.entries()
        del entries[i]
        self.set_entries(entries)

    def action_add(self) -> None:
        self.pick_group(None)

    def action_edit(self) -> None:
        i = self._selected()
        if i is not None:
            self.pick_group(i)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_edit()

    @work
    async def pick_group(self, index: int | None) -> None:
        entries = self.entries()
        cur = entries[index] if index is not None else None
        registry = harnesses()
        opts = []
        for name, cls in registry.items():
            mark = "" if cls.available() else f"  (install bannaha[{cls.extra}])"
            opts.append((name, name, f"{cls.summary}{mark}"))
        harness = await self.app.push_screen_wait(PickerModal("Harness", opts, cur.harness if cur else None))
        if not harness:
            return
        cls = registry[harness]
        bopts = [(b, b, BACKENDS.get(b, "")) for b in cls.backends]
        backend = await self.app.push_screen_wait(PickerModal(
            f"{harness}: backend", bopts, cur.backend if cur and cur.backend in cls.backends else None))
        if not backend:
            return
        hints = cls.model_hints.get(backend, [])
        mopts = [(m, m, "") for m in hints] + [(CUSTOM, "other…", "type a model id")]
        model = await self.app.push_screen_wait(PickerModal(
            f"{harness}@{backend}: model", mopts, cur.model if cur and cur.model in hints else None))
        if not model:
            return
        if model == CUSTOM:
            model = await self.app.push_screen_wait(InputModal(
                f"{harness}@{backend}: model id", cur.model if cur else "",
                "banna@direct takes vendor/model, e.g. anthropic/claude-haiku-4-5" if harness == "banna" else ""))
            if not model:
                return
        count_s = await self.app.push_screen_wait(InputModal("How many agents", str(cur.count if cur else 1)))
        try:
            count = max(1, int(count_s or 1))
        except ValueError:
            count = 1
        entry = FleetEntry(harness, backend, model, count)
        if index is None:
            entries.append(entry)
        else:
            entries[index] = entry
        self.set_entries(entries)
