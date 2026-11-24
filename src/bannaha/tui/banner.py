"""The header under the page bar: banna's scout mascot with the bannaha name.

banna and bannaha share one header (`banna_agent.cli.theme.brand_header`),
so the two tools read as one project; only the name differs. `ctrl+b` (or
Setup → Banner) hides it.
"""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..cli.themes import resolve


def header(page: str = "", theme: str | None = None) -> Text:
    """bannaha's header text: banna's mascot, the name, and the current page."""
    try:
        from banna_agent.cli.theme import brand_header
    except ImportError:  # banna ships with bannaha; keep the app usable if an install is broken
        return Text.assemble(("bannaha", "bold"), (f" — {page}" if page else "", "dim"))
    return brand_header("bannaha", page, resolve(theme))


class Banner(Static):
    DEFAULT_CSS = """
    Banner { height: auto; margin: 1 0 0 2; }
    """

    def __init__(self, page: str = "") -> None:
        super().__init__()
        self.page = page
        self._theme: str | None = None

    def on_mount(self) -> None:
        self.tick()
        self.set_interval(0.5, self.tick)  # follow the banner toggle and theme changes

    def _settings(self):
        return getattr(self.app, "settings", None)

    def tick(self) -> None:
        settings = self._settings()
        want = bool(getattr(settings, "banner", True))
        if self.display != want:
            self.display = want
        theme = getattr(settings, "theme", None)
        if want and theme != self._theme:
            self._theme = theme
            self.update(header(self.page, theme))
