"""Color themes. Semantic colors (ok, warn, error) are fixed across themes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str
    title: str
    border: str
    muted: str
    agent: str
    tool: str
    claim: str
    ok: str = "bold green"
    warn: str = "yellow"
    err: str = "bold red"


THEMES = {
    "ocean": Theme("ocean", accent="bold #5fd7ff", title="bold #afd7ff", border="#3a7ca5", muted="grey58",
                   agent="#87afff", tool="#5fafaf", claim="#d7d787"),
    "sunset": Theme("sunset", accent="bold #ffaf5f", title="bold #ffd7af", border="#d7875f", muted="grey58",
                    agent="#ff8787", tool="#d7af87", claim="#ffd75f"),
    "mono": Theme("mono", accent="bold white", title="bold white", border="grey50", muted="grey50",
                  agent="white", tool="grey70", claim="white"),
}
DEFAULT_THEME = "ocean"


def get_theme(name: str | None) -> Theme:
    return THEMES.get((name or DEFAULT_THEME).lower(), THEMES[DEFAULT_THEME])
