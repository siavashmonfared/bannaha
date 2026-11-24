"""Ranked-ballot counting.

Exact ties are position-biased by default: `borda` keeps the order of
`candidates`, so whichever candidate is listed first wins every exact tie.
Pass a seeded `random.Random` as `rng` to break ties in a random but
reproducible order instead, or use `tied_top` to see the tied set and let
the strategy decide.
"""
from __future__ import annotations

import random
from collections import Counter


def borda(ballots: dict[str, list[str]], candidates: list[str], *,
          rng: random.Random | None = None) -> list[tuple[str, int]]:
    """Borda count: a candidate ranked i-th of n gets n - i points. Highest score first."""
    n = len(candidates)
    scores = Counter({c: 0 for c in candidates})
    for ranking in ballots.values():
        for i, c in enumerate(ranking):
            if c in scores:
                scores[c] += n - i
    order = list(candidates)
    if rng is not None:
        rng.shuffle(order)
    position = {c: i for i, c in enumerate(order)}
    return sorted(scores.items(), key=lambda kv: (-kv[1], position[kv[0]]))


def tied_top(scores: list[tuple[str, int]]) -> list[str]:
    """Every candidate sharing the best score in a `borda` result (one id when there is no tie)."""
    if not scores:
        return []
    best = scores[0][1]
    return [c for c, s in scores if s == best]


def first_choices(ballots: dict[str, list[str]]) -> Counter:
    return Counter(r[0] for r in ballots.values() if r)


def leader(firsts: Counter, scores: list[tuple[str, int]]) -> tuple[str, int, list[str]]:
    """The first-choice leader as (id, first-place votes, ids tied on first-place votes).

    A tie on first choices goes to the higher Borda score, then to `scores`' own tie order, never to
    whichever ballot happened to arrive first.
    """
    if not firsts:
        top = scores[0][0] if scores else ""
        return top, 0, [top] if top else []
    most = max(firsts.values())
    tied = [c for c, v in firsts.items() if v == most]
    rank = {c: i for i, (c, _) in enumerate(scores)}
    tied.sort(key=lambda c: rank.get(c, len(rank)))
    return tied[0], most, tied
