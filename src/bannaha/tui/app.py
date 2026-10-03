"""The bannaha full-screen app: five pages, one shared settings object."""
from __future__ import annotations

from typing import ClassVar

from textual.app import App
from textual.binding import Binding

from ..cli.config import Settings, load_env_files, load_settings, save_settings
from ..cli.runner import problems_for
from ..core.fleet import parse_fleet
from .fleet import FleetScreen
from .keys import KeysScreen
from .run import RunScreen
from .runs import RunsScreen
from .setup import SetupScreen, _fmt_drop

TEXTUAL_THEMES = {"ocean": "tokyo-night", "sunset": "gruvbox", "mono": "textual-dark"}


class BannahaApp(App):
    TITLE = "bannaha"
    MODES: ClassVar = {"setup": SetupScreen, "fleet": FleetScreen, "keys": KeysScreen, "run": RunScreen,
                       "runs": RunsScreen}
    BINDINGS: ClassVar = [
        Binding("1", "switch_mode('setup')", "Setup"),
        Binding("2", "switch_mode('fleet')", "Fleet"),
        Binding("3", "switch_mode('keys')", "Keys"),
        Binding("4", "switch_mode('run')", "Run"),
        Binding("5", "switch_mode('runs')", "Runs"),
        Binding("ctrl+b", "toggle_banner", "banner", show=False, priority=True),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
    ]

    def __init__(self, start: str = "setup", settings: Settings | None = None) -> None:
        super().__init__()
        if settings is None:
            settings, _ = load_settings()
        self.settings = settings
        self.start_mode = start if start in self.MODES else "setup"

    def on_mount(self) -> None:
        self._apply_theme()
        self.switch_mode(self.start_mode)

    def _apply_theme(self) -> None:
        name = TEXTUAL_THEMES.get(self.settings.theme, "textual-dark")
        if name in self.available_themes:
            self.theme = name

    def action_toggle_banner(self) -> None:
        self.settings.banner = not self.settings.banner
        self.refresh_banners()

    def refresh_banners(self) -> None:
        from .banner import Banner

        for b in self.screen.query(Banner):
            b.display = self.settings.banner  # banners on other pages catch up on their next tick

    def problems(self) -> list[str]:
        return problems_for(self.settings)

    def summary(self) -> str:
        s = self.settings
        try:
            n = sum(e.count for e in parse_fleet(s.fleet))
        except Exception:  # invalid fleet spec: summarized as 0 agents
            n = 0
        python = s.sandbox if s.python else "off"
        return (f"{s.strategy} · {n} agent(s) · web {'on' if s.web else 'off'} · python {python} · "
                f"dropout {_fmt_drop(s.drop)}")

    def save(self) -> str:
        self._apply_theme()
        return str(save_settings(self.settings))


def run_tui(start: str = "setup") -> None:
    load_env_files()
    BannahaApp(start).run()
