"""Page 1: the settings dashboard. ↑↓ move, ←/→ toggle in place, ↵ edit."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ..cli.themes import THEMES
from ..core.fleet import fleet_summary, parse_fleet
from ..core.registry import strategies
from ..core.text import clip
from ..tools.web_search import search_key_present
from .banner import Banner
from .widgets import InputModal, PageBar, PickerModal

if TYPE_CHECKING:
    from .app import BannahaApp

ROWS = [
    ("strategy", "Strategy"), ("fleet", "Fleet"), ("web", "Web search"), ("python", "Python"),
    ("peer_updates", "Peer updates"), ("drop", "Dropout"), ("drop_after", "Drop after"),
    ("options", "Options"), ("limits", "Limits"), ("theme", "Theme"), ("banner", "Banner"),
]
# (settings field, label, type, help); 0 means "no limit" for the run-wide caps.
LIMITS = [
    ("max_calls", "tool calls / turn", int, "per agent turn; the turn ends when they run out"),
    ("max_steps", "model calls / turn", int, "model requests per agent turn"),
    ("max_output_tokens", "output tokens / reply", int, "cap on one model reply (reasoning tokens count)"),
    ("max_wall_s", "wall time (s)", float, "the run stops after this many seconds"),
    ("max_tokens", "tokens / run", int, "all agents together; 0 = no limit"),
    ("max_cost_usd", "cost / run ($)", float, "list prices (bannaha prices); the run stops here; 0 = no limit"),
    ("max_agent_cost_usd", "cost / agent ($)", float, "an agent that reaches it does no more work; 0 = no limit"),
]


def fmt_limits(s) -> str:
    def k(n: float) -> str:
        return f"{n / 1000:.0f}k" if n >= 1000 else f"{n:g}"

    def cap(v: float, prefix: str = "") -> str:
        return f"{prefix}{k(v) if not prefix else f'{v:g}'}" if v else "∞"

    return (f"{s.max_calls} calls · {s.max_steps} steps · {k(s.max_output_tokens)} out · {s.max_wall_s:g}s · "
            f"tokens {cap(s.max_tokens)} · run {cap(s.max_cost_usd, '$')} · agent {cap(s.max_agent_cost_usd, '$')}")


DROP_STEPS = [0.0, 0.25, 0.5, 0.75]
PYTHON_STEPS = ["off", "process", "docker"]


def _fmt_drop(v: float) -> str:
    if not v:
        return "off"
    return f"{v:.0%}" if v < 1 else f"{int(v)} agents"


class SetupScreen(Screen):
    BINDINGS: ClassVar = [
        Binding("left", "cycle(-1)", "toggle ←"),
        Binding("right", "cycle(1)", "toggle →"),
        Binding("s", "start", "start"),
        Binding("d", "save_start", "save as default & start"),
        Binding("q", "app.quit", "quit"),
    ]
    DEFAULT_CSS = """
    SetupScreen #frame { width: 100; height: auto; margin: 1 2 0 2; border: round $accent; padding: 1 2; }
    SetupScreen #rows { height: auto; border: none; }
    SetupScreen #status { margin: 1 4; height: auto; }
    SetupScreen .hint { color: $text-muted; margin: 0 4; }
    """
    AUTO_FOCUS = "#rows"

    @property
    def bapp(self) -> BannahaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield PageBar("setup")
        yield Banner()
        with Vertical(id="frame"):
            yield OptionList(id="rows")
        yield Static(id="status")
        yield Static("↑↓ move · ←/→ toggle · ↵ edit · s start · d save as default & start · 1–5 pages · q quit",
                     classes="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_rows()

    def on_screen_resume(self) -> None:
        self.refresh_rows()

    # -- rendering ----------------------------------------------------------

    def _value(self, key: str) -> tuple[str, str]:
        s = self.bapp.settings
        if key == "strategy":
            cls = strategies().get(s.strategy)
            return s.strategy, (cls.summary if cls else "unknown strategy")
        if key == "fleet":
            try:
                entries = parse_fleet(s.fleet)
                n = sum(e.count for e in entries)
                return fleet_summary(entries), f"{n} agent(s) · ↵ edit on the Fleet page"
            except Exception as exc:  # bad saved spec: show it instead of crashing
                return s.fleet, f"invalid: {exc}"
        if key == "web":
            hint = "Gemini grounding · " + ("key found" if search_key_present() else "needs GEMINI_API_KEY")
            return ("on" if s.web else "off"), hint
        if key == "python":
            return (s.sandbox if s.python else "off"), "run_python: off · process (host) · docker (isolated)"
        if key == "peer_updates":
            return ("on" if s.peer_updates else "off"), "agents see peers' new board activity mid-turn"
        if key == "drop":
            return _fmt_drop(s.drop), "remove agents mid-run (fault injection)"
        if key == "drop_after":
            return f"{s.drop_after} call(s)", "dropped agents go dark after this many tool calls"
        if key == "options":
            cls = strategies().get(s.strategy)
            if cls is None or not cls.options:
                return "none", "this strategy has no options"
            vals = {**{k: o.default for k, o in cls.options.items()}, **s.options.get(s.strategy, {})}
            return " ".join(f"{k}={v}" for k, v in vals.items()), "↵ edit"
        if key == "limits":
            return fmt_limits(s), "↵ edit"
        if key == "theme":
            return s.theme, "colors of the run panels"
        if key == "banner":
            return ("on" if s.banner else "off"), "the animated header (ctrl+b toggles it anywhere)"
        return "", ""

    def _row(self, key: str, label: str) -> Text:
        value, hint = self._value(key)
        width = 90
        cyclable = key not in ("fleet", "options", "limits")
        shown = clip(value, width - 24)
        shown = f"‹ {shown} ›" if cyclable else shown
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(f"  {label:<14}", "bold")
        t.append(shown, "bold")
        room = width - 16 - len(shown) - 3
        if hint and room > 8:
            t.append(f"   {clip(hint, room)}", "dim")
        return t

    def refresh_rows(self) -> None:
        ol = self.query_one("#rows", OptionList)
        old = ol.highlighted
        ol.clear_options()
        for key, label in ROWS:
            ol.add_option(Option(self._row(key, label), id=key))
        ol.add_option(Option(Text("─" * 90, "dim"), disabled=True, id="__sep__"))
        ol.add_option(Option(Text("  [ start ]  (s)              [ save as default & start ]  (d)", "bold"),
                             id="__start__"))
        ol.highlighted = old if old is not None else 0
        problems = self.bapp.problems()
        status = Text()
        if problems:
            status.append("✗ cannot start yet\n", "bold red")
            for p in problems:
                status.append(f"  • {p}\n", "red")
            status.append("  Missing keys? Open the Keys page (3).", "dim")
        else:
            status.append("✓ ready", "bold green")
            status.append(f"  ·  {self.bapp.summary()}", "dim")
        self.query_one("#status", Static).update(status)

    # -- actions ------------------------------------------------------------

    def _current(self) -> str | None:
        ol = self.query_one("#rows", OptionList)
        if ol.highlighted is None:
            return None
        return ol.get_option_at_index(ol.highlighted).id

    def action_cycle(self, step: int) -> None:
        key = self._current()
        s = self.bapp.settings
        if key == "strategy":
            names = list(strategies())
            s.strategy = names[(names.index(s.strategy) + step) % len(names)] if s.strategy in names else names[0]
        elif key == "web":
            s.web = not s.web
        elif key == "python":
            cur = s.sandbox if s.python else "off"
            nxt = PYTHON_STEPS[(PYTHON_STEPS.index(cur) + step) % len(PYTHON_STEPS)]
            s.python = nxt != "off"
            if nxt != "off":
                s.sandbox = nxt
        elif key == "peer_updates":
            s.peer_updates = not s.peer_updates
        elif key == "drop":
            steps = DROP_STEPS if s.drop in DROP_STEPS else sorted({*DROP_STEPS, s.drop})
            s.drop = steps[(steps.index(s.drop) + step) % len(steps)]
        elif key == "drop_after":
            s.drop_after = max(0, s.drop_after + step)
        elif key == "banner":
            s.banner = not s.banner
            self.app.refresh_banners()
        elif key == "theme":
            names = list(THEMES)
            s.theme = names[(names.index(s.theme) + step) % len(names)] if s.theme in names else names[0]
        else:
            return
        self.refresh_rows()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id
        if key == "__start__":
            self.action_start()
        elif key == "fleet":
            self.app.switch_mode("fleet")
        elif key in ("web", "peer_updates", "banner"):
            self.action_cycle(1)
        else:
            self.edit(key)

    @work
    async def edit(self, key: str) -> None:
        s = self.bapp.settings
        if key == "strategy":
            opts = [(n, n, c.summary) for n, c in strategies().items()]
            value = await self.app.push_screen_wait(PickerModal("Strategy", opts, s.strategy))
            if value:
                s.strategy = value
        elif key == "python":
            opts = [("off", "off", "no run_python tool"), ("process", "process", "host subprocess, wall-time limit"),
                    ("docker", "docker", "throwaway container: no network, read-only, resource caps")]
            current = s.sandbox if s.python else "off"
            value = await self.app.push_screen_wait(PickerModal("Python sandbox", opts, current))
            if value:
                s.python = value != "off"
                if value != "off":
                    s.sandbox = value
        elif key == "drop":
            value = await self.app.push_screen_wait(InputModal(
                "Dropout", str(s.drop or 0), "a fraction below 1 (0.5 = half the eligible agents) or a count; 0 = off"))
            if value is not None:
                try:
                    s.drop = max(0.0, float(value))
                except ValueError:
                    self.notify(f"not a number: {value!r}", severity="error")
        elif key == "drop_after":
            value = await self.app.push_screen_wait(InputModal("Drop after (tool calls)", str(s.drop_after)))
            if value is not None:
                try:
                    s.drop_after = max(0, int(value))
                except ValueError:
                    self.notify(f"not a whole number: {value!r}", severity="error")
        elif key == "options":
            await self._edit_options()
        elif key == "limits":
            await self._edit_limits()
        elif key == "theme":
            opts = [(n, n, "") for n in THEMES]
            value = await self.app.push_screen_wait(PickerModal("Theme", opts, s.theme))
            if value:
                s.theme = value
        self.refresh_rows()

    async def _edit_options(self) -> None:
        s = self.bapp.settings
        cls = strategies().get(s.strategy)
        if cls is None or not cls.options:
            self.notify("this strategy has no options")
            return
        while True:
            current: dict[str, Any] = {**{k: o.default for k, o in cls.options.items()},
                                       **s.options.get(s.strategy, {})}
            opts = [(k, f"{k} = {current[k]}", o.help) for k, o in cls.options.items()]
            key = await self.app.push_screen_wait(PickerModal(f"{s.strategy} options  (Esc when done)", opts))
            if not key:
                return
            value = await self.app.push_screen_wait(InputModal(
                f"{s.strategy}: {key}", str(current[key]), cls.options[key].help))
            if value is None:
                continue
            s.options.setdefault(s.strategy, {})[key] = value
            problems = self.bapp.problems()
            if any("option" in p for p in problems):
                self.notify(problems[0], severity="error")
                s.options[s.strategy].pop(key, None)

    async def _edit_limits(self) -> None:
        s = self.bapp.settings
        spec = {f: (label, typ, hint) for f, label, typ, hint in LIMITS}
        while True:
            opts = [(f, f"{label} = {getattr(s, f):g}", hint) for f, label, _, hint in LIMITS]
            key = await self.app.push_screen_wait(PickerModal("Limits  (Esc when done)", opts))
            if not key:
                return
            label, typ, hint = spec[key]
            value = await self.app.push_screen_wait(InputModal(label, f"{getattr(s, key):g}", hint))
            if value is None:
                continue
            try:
                v = typ(float(value)) if typ is int else typ(value)
            except ValueError:
                self.notify(f"not a number: {value!r}", severity="error")
                continue
            if v < 0 or (v == 0 and key in ("max_calls", "max_steps", "max_output_tokens", "max_wall_s")):
                self.notify(f"{label} must be above 0", severity="error")
                continue
            setattr(s, key, v)

    def action_start(self) -> None:
        problems = self.bapp.problems()
        if problems:
            self.notify(problems[0], title="cannot start", severity="error")
            return
        self.app.switch_mode("run")

    def action_save_start(self) -> None:
        path = self.bapp.save()
        self.notify(f"saved {path}")
        self.action_start()
