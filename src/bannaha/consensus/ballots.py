from collections import Counter


def borda(ballots: dict[str, list[str]], candidates: list[str]) -> list[tuple[str, int]]:
    """Borda count: a candidate ranked i-th of n gets n - i points."""
    n = len(candidates)
    scores = Counter({c: 0 for c in candidates})
    for ranking in ballots.values():
        for i, c in enumerate(ranking):
            if c in scores:
                scores[c] += n - i
    return sorted(scores.items(), key=lambda kv: (-kv[1], candidates.index(kv[0])))


def first_choices(ballots: dict[str, list[str]]) -> Counter:
    return Counter(r[0] for r in ballots.values() if r)
