"""Color themes, shared with banna so the two tools look like one project.

The palettes are banna's (`banna_agent.cli.themes`): every theme fills the
same Solarized slot names (base3 = background, base2 = panel, base1 = muted,
base01 = emphasis, green = agent accent, ...). `get_theme` turns a palette
into the semantic roles bannaha paints with, and `textual_theme_specs`
gives the full-screen app the same colors banna's launch TUI registers.

Keep the palettes in sync with banna when either project adds a theme.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Palettes: identical to banna's, same slot names as Solarized
# ---------------------------------------------------------------------------

_SOLARIZED_LIGHT = {
    "base03": "#002b36", "base02": "#073642",
    "base01": "#586e75",   # emphasis / agent prose
    "base00": "#657b83",   # body
    "base0":  "#839496",
    "base1":  "#93a1a1",   # muted
    "base2":  "#eee8d5",   # panel
    "base3":  "#fdf6e3",   # background
    "yellow": "#b58900", "orange": "#cb4b16", "red": "#dc322f",
    "magenta": "#d33682", "violet": "#6c71c4", "blue": "#268bd2",
    "cyan": "#2aa198", "green": "#859900",
}

# Same hues, light/dark roles swapped (canonical Solarized Dark).
_SOLARIZED_DARK = {
    **_SOLARIZED_LIGHT,
    "base3":  "#002b36",   # background
    "base2":  "#073642",   # panel
    "base1":  "#586e75",   # muted
    "base01": "#93a1a1",   # emphasis
    "base00": "#839496",   # body
    "base0":  "#657b83",
    "base03": "#fdf6e3",
    "base02": "#eee8d5",
}

_GRUVBOX_DARK = {
    "base03": "#fbf1c7", "base02": "#ebdbb2",
    "base01": "#ebdbb2",   # emphasis
    "base00": "#d5c4a1",   # body
    "base0":  "#bdae93",
    "base1":  "#928374",   # muted
    "base2":  "#3c3836",   # panel
    "base3":  "#282828",   # background
    "yellow": "#fabd2f", "orange": "#fe8019", "red": "#fb4934",
    "magenta": "#d3869b", "violet": "#d3869b", "blue": "#83a598",
    "cyan": "#8ec07c", "green": "#b8bb26",
}

_DRACULA = {
    "base03": "#f8f8f2", "base02": "#f8f8f2",
    "base01": "#f8f8f2",   # emphasis
    "base00": "#e6e6dc",   # body
    "base0":  "#bfbfb2",
    "base1":  "#6272a4",   # muted
    "base2":  "#44475a",   # panel
    "base3":  "#282a36",   # background
    "yellow": "#f1fa8c", "orange": "#ffb86c", "red": "#ff5555",
    "magenta": "#ff79c6", "violet": "#bd93f9", "blue": "#8be9fd",
    "cyan": "#8be9fd", "green": "#50fa7b",
}

PALETTES: dict[str, dict[str, str]] = {
    "solarized-light": _SOLARIZED_LIGHT,
    "solarized-dark": _SOLARIZED_DARK,
    "gruvbox-dark": _GRUVBOX_DARK,
    "dracula": _DRACULA,
    "mono": {},   # sentinel: no color
}

THEME_BLURBS: dict[str, str] = {
    "solarized-light": "the banna default — warm paper background",
    "solarized-dark": "same hues on the dark base",
    "gruvbox-dark": "retro warm dark",
    "dracula": "high-contrast dark purple",
    "mono": "no color — pipes, screen readers, minimal terminals",
}

DEFAULT_THEME = "solarized-light"

# Names used before bannaha adopted banna's palettes; old settings files keep working.
LEGACY_NAMES = {"ocean": "solarized-dark", "sunset": "gruvbox-dark"}


def resolve(name: str | None) -> str:
    key = (name or DEFAULT_THEME).lower()
    key = LEGACY_NAMES.get(key, key)
    return key if key in PALETTES else DEFAULT_THEME


def get_palette(name: str | None = None) -> dict[str, str]:
    """Palette for `name`. Mono returns the Solarized Light slots so drawing code always has hex values."""
    return PALETTES.get(resolve(name)) or _SOLARIZED_LIGHT


# ---------------------------------------------------------------------------
# Semantic roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str      # prompts, roles, highlights (banna: scout.accent)
    title: str       # the product name in headers (banna: scout.title)
    border: str      # panel borders and rules (banna: scout.border)
    muted: str       # secondary text (banna: scout.muted)
    agent: str       # default agent color (banna: banna.agent)
    tool: str        # tool names and in-flight work (banna: scout.info)
    claim: str       # board claims (banna: scout.reasoning)
    text: str = ""   # emphasized prose (banna: scout.text)
    ok: str = "bold green"
    warn: str = "yellow"
    err: str = "bold red"
    you: str = "bold"  # the user's own input (banna: banna.you)
    agent_colors: tuple[str, ...] = ("blue", "magenta", "cyan", "yellow", "green", "red")


def _from_palette(name: str, p: dict[str, str]) -> Theme:
    return Theme(
        name=name, accent=f"bold {p['green']}", title=f"bold {p['green']}", border=p["base1"],
        muted=p["base1"], agent=p["green"], tool=p["cyan"], claim=p["violet"], text=p["base01"],
        ok=f"bold {p['green']}", warn=p["yellow"], err=f"bold {p['red']}", you=f"bold {p['orange']}",
        agent_colors=(p["blue"], p["orange"], p["green"], p["magenta"], p["yellow"], p["cyan"], p["violet"],
                      p["red"]),
    )


_MONO = Theme("mono", accent="bold", title="bold", border="", muted="dim", agent="", tool="", claim="",
              text="", ok="bold", warn="bold", err="bold", you="bold", agent_colors=("",))

THEMES: dict[str, Theme] = {
    name: (_MONO if name == "mono" else _from_palette(name, p)) for name, p in PALETTES.items()
}


def get_theme(name: str | None) -> Theme:
    return THEMES[resolve(name)]


# ---------------------------------------------------------------------------
# Textual: the same themes banna's launch TUI registers
# ---------------------------------------------------------------------------


def textual_name(name: str | None) -> str:
    key = resolve(name)
    return "textual-ansi" if key == "mono" else f"banna-{key}"


def textual_theme_specs() -> list[dict[str, object]]:
    """Keyword arguments for `textual.theme.Theme`, one per palette (mono uses the built-in ANSI theme)."""
    specs = []
    for name, p in PALETTES.items():
        if name == "mono":
            continue
        specs.append({
            "name": f"banna-{name}", "primary": p["blue"], "secondary": p["violet"], "warning": p["yellow"],
            "error": p["red"], "success": p["green"], "accent": p["orange"], "foreground": p["base01"],
            "background": p["base3"], "surface": p["base2"], "panel": p["base2"],
            "dark": "dark" in name or name == "dracula",
        })
    return specs
