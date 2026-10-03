"""The animated header: a rotating ASCII torus and a construction worker who
runs in, climbs it, hammers on top and jumps off, next to the bannaha banner.

Adapted from the standalone bannaha_spin.py splash, scaled down to a strip
that sits under the page bar. `ctrl+b` (or Setup → Banner) hides it.
"""
from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

RAMP = " .:-+*=%@#"  # dark -> bright
R1, R2, K2 = 1.0, 2.0, 5.0  # tube radius, ring radius, viewer distance

BANNER_BIG = r"""
██████╗  █████╗ ███╗   ██╗███╗   ██╗ █████╗ ██╗  ██╗ █████╗
██╔══██╗██╔══██╗████╗  ██║████╗  ██║██╔══██╗██║  ██║██╔══██╗
██████╔╝███████║██╔██╗ ██║██╔██╗ ██║███████║███████║███████║
██╔══██╗██╔══██║██║╚██╗██║██║╚██╗██║██╔══██║██╔══██║██╔══██║
██████╔╝██║  ██║██║ ╚████║██║ ╚████║██║  ██║██║  ██║██║  ██║
╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝""".strip("\n").split("\n")
BANNER_SMALL = r"""
 ___   _   _  _ _  _   _   _  _   _
| _ ) /_\ | \| | \| | /_\ | || | /_\
| _ \/ _ \| .` | .` |/ _ \| __ |/ _ \
|___/_/ \_\_|\_|_|\_/_/ \_\_||_/_/ \_\
""".strip("\n").split("\n")
TAGLINE = "construction crew for AI agents  ·  structure emerges"

# Worker sprite. Each frame: (text, colour mask). Mask letters: g green (cap/shirt), b blue (overalls),
# s skin, w white, k grey (hammer), y yellow (sparks), '.' default; spaces are transparent.
_TOP = [("  .---. ", "  gggg  "),
        ("  |_B_|_", "  ggwggg"),
        ("   (o > ", "   ssss "),
        ("   ~~~  ", "   kkk  ")]
_BODY = ("  /|##|\\", "  gbbbbg")
_HIPS = ("   |##| ", "   bbbb ")
LEGS = {
    "a": ("   /  \\ ", "   b  b "),
    "b": ("   |  / ", "   b  b "),
    "c": ("   \\  | ", "   b  b "),
    "s": ("   |  | ", "   b  b "),
}
RUN = [_TOP + [_BODY, _HIPS, LEGS[k]] for k in "abcb"]
CLIMB = [
    _TOP + [("   |##|=", "   bbbbs"), ("   |##| ", "   bbbb "), LEGS["b"]],
    _TOP + [("   |##|-", "   bbbbs"), ("   |##| ", "   bbbb "), LEGS["c"]],
]
HAMMER = [
    [("  .---. T", "  gggg  k"),
     ("  |_B_|_|", "  ggwgggk"),
     ("   (o > /", "   ssss g"),
     ("   ~~~ / ", "   kkk g "),
     ("   |##|  ", "   bbbb  "), _HIPS, LEGS["s"]],
    _TOP + [("  /|##|\\_T", "  gbbbbggk"), _HIPS, ("   |  | *", "   b  b y")],
]
JUMP = [_TOP + [("  \\|##|/", "  gbbbbg"), _HIPS, ("  _/  \\_", "  bb  bb")]]
SPRITE_H = 7
COLORS = {"g": "color(34)", "b": "color(27)", "s": "color(223)", "w": "bold bright_white", "k": "color(245)",
          "y": "bold bright_yellow"}
GROUND_STYLE = "color(94)"
BANNER_STYLE = "bold bright_yellow"
TAGLINE_STYLE = "color(245)"


class Scene:
    """Torus + worker on a w x h canvas (plus a ground row)."""

    def __init__(self, w: int = 62, h: int = 14) -> None:
        self.w, self.h = w, h
        self.cx = int(w * 0.6)
        self.k1 = (h - SPRITE_H + 0.5) / 0.78  # torus ~ h - 7 rows tall, leaving headroom for the worker
        self.cy = h - 0.6 - 0.375 * self.k1  # rests on the ground row
        self.th = [(math.cos(t), math.sin(t)) for t in (i * 0.12 for i in range(int(2 * math.pi / 0.12) + 1))]
        self.ph = [(math.cos(p), math.sin(p)) for p in (i * 0.045 for i in range(int(2 * math.pi / 0.045) + 1))]
        self.a, self.b = 1.0, 0.0
        self.reset()

    # -- torus (donut.c style) -------------------------------------------------

    def torus(self) -> tuple[list[list[str]], list[list[float]]]:
        w, h = self.w, self.h
        out = [[" "] * w for _ in range(h)]
        lum = [[0.0] * w for _ in range(h)]
        zb = [[0.0] * w for _ in range(h)]
        ca, sa, cb, sb = math.cos(self.a), math.sin(self.a), math.cos(self.b), math.sin(self.b)
        for ct, st in self.th:
            cx, cy = R2 + R1 * ct, R1 * st
            for cp, sp in self.ph:
                x = cx * (cb * cp + sa * sb * sp) - cy * ca * sb
                y = cx * (sb * cp - sa * cb * sp) + cy * ca * cb
                ooz = 1.0 / (K2 + ca * cx * sp + cy * sa)
                xp = int(self.cx + self.k1 * ooz * x)
                yp = int(self.cy - self.k1 * ooz * y * 0.5)
                if not (0 <= xp < w and 0 <= yp < h) or ooz <= zb[yp][xp]:
                    continue
                lval = max(0.0, cp * ct * sb - ca * ct * sp - sa * st + cb * (ca * st - ct * sa * sp))
                zb[yp][xp] = ooz
                out[yp][xp] = RAMP[min(len(RAMP) - 1, 1 + int(lval * 6.4))]
                lum[yp][xp] = lval
        return out, lum

    # -- worker ---------------------------------------------------------------

    def reset(self) -> None:
        self.phase, self.x, self.y, self.t = "run", -9.0, float(self.h - SPRITE_H), 0
        self.vx = self.vy = 0.0

    def step_worker(self, grid: list[list[str]]) -> list[tuple[str, str]]:
        w, h = self.w, self.h
        left = [next((x for x in range(w) if grid[y][x] != " "), None) for y in range(h)]
        top = [next((y for y in range(h) if grid[y][x] != " "), None) for x in range(w)]
        tops = [t for t in top if t is not None]
        self.t += 1
        foot = int(self.y) + SPRITE_H - 1
        if self.phase == "run":
            edge = min((left[r] for r in range(max(0, foot - 3), min(h, foot + 1)) if left[r] is not None),
                       default=self.cx - 10)
            self.x += 1.0
            if self.x + 8 >= edge:
                self.phase = "climb"
            return RUN[(self.t // 2) % len(RUN)]
        if self.phase == "climb":
            if self.t % 2 == 0:
                self.y -= 1
            rows = [left[r] for r in range(max(0, int(self.y)), min(h, int(self.y) + SPRITE_H)) if left[r] is not None]
            if rows:
                self.x = min(rows) - 8
            if int(self.y) + SPRITE_H <= (min(tops) if tops else 0) + 1 or self.y < -SPRITE_H:
                self.phase, self.t = "ontop", 0
            return CLIMB[(self.t // 3) % 2]
        if self.phase == "ontop":  # walk to the crown, then hammer
            target = self.cx - 4
            if self.x < target:
                self.x += 0.6
            col = min(w - 1, max(0, int(self.x) + 4))
            surf = top[col] if top[col] is not None else (min(tops) if tops else h)
            self.y = max(0, surf - SPRITE_H)
            if self.x < target:
                return RUN[(self.t // 2) % len(RUN)]
            if self.t > 90:
                self.phase, self.vx, self.vy = "jump", 0.9, -0.6
            return HAMMER[(self.t // 4) % 2]
        if self.phase == "jump":  # arc off the right side
            self.x += self.vx
            self.y += self.vy
            self.vy += 0.12
            if self.y >= h - SPRITE_H:
                self.y, self.phase = float(h - SPRITE_H), "runoff"
            return JUMP[0]
        self.x += 1.2
        if self.x > w + 2:
            self.reset()
        return RUN[(self.t // 2) % len(RUN)]

    # -- frame ----------------------------------------------------------------

    def frame(self) -> list[list[tuple[str, str]]]:
        """One frame as rows of (char, style), h + 1 rows (the last is the ground)."""
        grid, lum = self.torus()
        sprite = self.step_worker(grid)
        sx, sy = int(self.x), int(self.y)
        w, h = self.w, self.h
        rows: list[list[tuple[str, str]]] = []
        for y in range(h):
            rows.append([(ch, f"color({min(255, 236 + int(lum[y][x] * 14))})" if ch != " " else "")
                         for x, ch in enumerate(grid[y])])
        rows.append([("▔", GROUND_STYLE)] * w)
        for dy, (txt, msk) in enumerate(sprite):
            yy = sy + dy
            if not 0 <= yy <= h:
                continue
            for dx, ch in enumerate(txt):
                xx = sx + dx
                if ch != " " and 0 <= xx < w:
                    m = msk[dx] if dx < len(msk) else "."
                    rows[yy][xx] = (ch, COLORS.get(m, ""))
        self.a += 0.07
        self.b += 0.03
        return rows


def render(scene: Scene, width: int) -> Text:
    """The scene with the banner and tagline to its right, if there is room."""
    rows = scene.frame()
    big = width >= scene.w + 2 + len(BANNER_BIG[0])
    small = width >= scene.w + 2 + len(TAGLINE)
    lines = (BANNER_BIG if big else BANNER_SMALL) if (big or small) else []
    side: list[tuple[str, str]] = [(ln, BANNER_STYLE) for ln in lines]
    if side:
        side.append(("", ""))
        side.append((TAGLINE, TAGLINE_STYLE))
    top = max(0, (len(rows) - len(side)) // 2)
    text = Text(no_wrap=True, overflow="crop")
    for i, row in enumerate(rows):
        run, style = [], None
        for ch, st in row:
            if st != style and run:
                text.append("".join(run), style or None)
                run = []
            style = st
            run.append(ch)
        if run:
            text.append("".join(run), style or None)
        j = i - top
        if 0 <= j < len(side):
            text.append("  " + side[j][0], side[j][1] or None)
        if i < len(rows) - 1:
            text.append("\n")
    return text


class Banner(Static):
    DEFAULT_CSS = """
    Banner { height: 15; padding: 0 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.scene = Scene()

    def on_mount(self) -> None:
        self.display = self._wanted()
        self.set_interval(1 / 12, self.tick)

    def _wanted(self) -> bool:
        settings = getattr(self.app, "settings", None)
        return bool(getattr(settings, "banner", True))

    def tick(self) -> None:
        want = self._wanted()
        if self.display != want:
            self.display = want
        if not want or self.app.screen is not self.screen:
            return
        self.update(render(self.scene, self.size.width or 120))
