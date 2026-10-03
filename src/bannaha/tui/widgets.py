"""Shared pieces: the page bar, and modal pickers / text entry."""
from __future__ import annotations

import textwrap
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

PAGES = [("setup", "Setup"), ("fleet", "Fleet"), ("keys", "Keys"), ("run", "Run"), ("runs", "Runs")]


class PageBar(Static):
    """`bannaha  1 Setup  2 Fleet  3 Keys  4 Run  5 Runs` with the current page highlighted."""

    DEFAULT_CSS = """
    PageBar { height: 1; padding: 0 1; background: $panel; }
    """

    def __init__(self, active: str) -> None:
        super().__init__()
        self.active = active

    def _segments(self) -> list[tuple[int, int, str]]:
        """(start column, end column, page) for each page tab, matching render()."""
        x, out = 1 + len(" bannaha ") + 2, []
        for i, (key, label) in enumerate(PAGES, 1):
            width = len(f" {i} {label} ")
            out.append((x, x + width, key))
            x += width + 1
        return out

    def render(self) -> Text:
        t = Text()
        t.append(" bannaha ", "bold reverse")
        t.append("  ")
        for i, (key, label) in enumerate(PAGES, 1):
            style = "bold reverse" if key == self.active else "dim"
            t.append(f" {i} {label} ", style)
            t.append(" ")
        t.append("  press 1–5 or click to switch", "dim italic")
        return t

    def on_click(self, event) -> None:
        for start, end, key in self._segments():
            if start <= event.x < end:
                self.app.switch_mode(key)
                return


class PickerModal(ModalScreen[str | None]):
    """Pick one value from a list. Each option is (value, label, hint)."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "back")]
    DEFAULT_CSS = """
    PickerModal { align: center middle; }
    PickerModal > Vertical { width: 90; max-height: 80%; height: auto; border: round $accent;
                             background: $surface; padding: 1 2; }
    PickerModal Label { margin-bottom: 1; text-style: bold; }
    PickerModal OptionList { height: auto; max-height: 24; border: none; }
    """

    def __init__(self, title: str, options: list[tuple[str, str, str]], current: str | None = None) -> None:
        super().__init__()
        self.title_text = title
        self.options = options
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text)
            yield OptionList(id="picker")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        for value, label, hint in self.options:
            t = Text()
            t.append("● " if value == self.current else "  ", "bold green")
            if hint and len(label) + len(hint) > 68:
                t.append(label, "bold")
                for line in textwrap.wrap(hint, 68):
                    t.append(f"\n    {line}", "dim")
            else:
                t.append(f"{label:<28}", "bold")
                t.append(hint, "dim")
            ol.add_option(Option(t, id=value))
        values = [o[0] for o in self.options]
        ol.highlighted = values.index(self.current) if self.current in values else 0
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class InputModal(ModalScreen[str | None]):
    """Type a value. Enter accepts, Esc cancels."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "back")]
    DEFAULT_CSS = """
    InputModal { align: center middle; }
    InputModal > Vertical { width: 80; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    InputModal Label { margin-bottom: 1; }
    InputModal .hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, title: str, value: str = "", hint: str = "") -> None:
        super().__init__()
        self.title_text, self.value, self.hint = title, value, hint

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self.title_text, "bold"))
            yield Input(value=self.value, id="value")
            if self.hint:
                yield Label(self.hint, classes="hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)
