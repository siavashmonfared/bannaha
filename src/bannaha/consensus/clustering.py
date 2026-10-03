"""Group answers that mean the same thing.

Numbers are parsed with scale words, currency and percent ("$2.9bn",
"2,900 million USD") and merged within a relative tolerance when their
unit classes match. Text answers are normalized (case, punctuation,
articles) and merged when their token sets are nearly identical.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_SCALES = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6, "millions": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9, "billions": 1e9,
    "t": 1e12, "tn": 1e12, "trillion": 1e12, "trillions": 1e12,
}
_CURRENCY = {"$": "usd", "usd": "usd", "us$": "usd", "dollar": "usd", "dollars": "usd",
             "€": "eur", "eur": "eur", "euro": "eur", "euros": "eur",
             "£": "gbp", "gbp": "gbp", "pound": "gbp", "pounds": "gbp"}
_PERCENT = {"%", "percent", "pct", "percentage"}
_NUM = re.compile(
    r"^\s*(?P<cur>us\$|\$|€|£)?\s*(?P<num>[-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+)\s*"
    r"(?:(?P<scale>thousand|millions?|billions?|trillions?|mm|mn|bn|tn|k|m|b|t)\b)?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_ARTICLES = {"a", "an", "the"}


@dataclass(frozen=True)
class Parsed:
    numeric: float | None
    unit: str
    text: str


def _unit_key(raw: str) -> tuple[str, float]:
    tokens = re.findall(r"us\$|[$€£%]|[a-z]+", raw.lower())
    scale = 1.0
    unit_parts: list[str] = []
    for tok in tokens:
        if tok in _SCALES:
            scale *= _SCALES[tok]
        elif tok in _CURRENCY:
            unit_parts.append(_CURRENCY[tok])
        elif tok in _PERCENT:
            unit_parts.append("%")
        elif tok not in ("of", "in", "per", "approximately", "about", "roughly", "around"):
            unit_parts.append(tok)
    return " ".join(dict.fromkeys(unit_parts)), scale


def normalize_text(s: str) -> str:
    words = re.sub(r"[^\w\s%.-]", " ", s.lower()).replace("-", " ").split()
    return " ".join(w.strip(".") for w in words if w.strip(".") and w not in _ARTICLES)


def parse_answer(value: str, unit: str = "") -> Parsed:
    value = (value or "").strip()
    m = _NUM.match(value)
    if m:
        rest = m.group("rest") or ""
        rest_unit, rest_scale = _unit_key(rest)
        given_unit, given_scale = _unit_key(unit or "")
        # Treat as numeric only if what follows the number is unit-like (short).
        if len(rest.split()) <= 3:
            num = float(m.group("num").replace(",", ""))
            scale = _SCALES.get((m.group("scale") or "").lower(), 1.0) * rest_scale * given_scale
            cur = _CURRENCY.get((m.group("cur") or "").lower(), "")
            unit_key = " ".join(dict.fromkeys(p for p in (cur, rest_unit, given_unit) if p))
            return Parsed(numeric=num * scale, unit=unit_key, text=normalize_text(value))
    return Parsed(numeric=None, unit=_unit_key(unit or "")[0], text=normalize_text(value))


@dataclass
class Cluster:
    id: str
    display: str
    unit: str
    members: list[str] = field(default_factory=list)
    values: list[tuple[str, str]] = field(default_factory=list)
    center: float | None = None
    unit_key: str = ""
    tokens: frozenset[str] = frozenset()

    @property
    def support(self) -> int:
        return len(self.members)

    def view(self) -> dict:
        return {"id": self.id, "value": self.display, "unit": self.unit,
                "support": self.support, "agents": list(self.members)}


def _same_numeric(a: float, b: float, rel_tol: float) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= rel_tol


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


def cluster_answers(items: list[tuple[str, str, str]], *, rel_tol: float = 0.005,
                    text_similarity: float = 0.85) -> list[Cluster]:
    """Cluster `(member_id, value, unit)` items, largest cluster first."""
    clusters: list[Cluster] = []
    for member, value, unit in items:
        p = parse_answer(value, unit)
        tokens = frozenset(p.text.split())
        target = None
        for c in clusters:
            if p.numeric is not None and c.center is not None:
                same_unit = not p.unit or not c.unit_key or p.unit == c.unit_key
                if same_unit and _same_numeric(p.numeric, c.center, rel_tol):
                    target = c
                    break
            elif p.numeric is None and c.center is None:
                if p.text == normalize_text(c.display) or _jaccard(tokens, c.tokens) >= text_similarity:
                    target = c
                    break
        if target is None:
            target = Cluster(id="", display=value.strip(), unit=unit.strip(), center=p.numeric, tokens=tokens)
            clusters.append(target)
        if not target.unit_key:
            target.unit_key = p.unit
        target.members.append(member)
        target.values.append((value.strip(), unit.strip()))
    for c in clusters:
        c.display, c.unit = Counter(c.values).most_common(1)[0][0]
    clusters.sort(key=lambda c: -c.support)
    for i, c in enumerate(clusters, 1):
        c.id = f"k{i}"
    return clusters
