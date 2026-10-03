import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Quorum:
    """A cluster wins when it has at least `fraction` of the alive agents
    and at least `min_count` of them (capped at the number alive)."""

    fraction: float = 0.5
    min_count: int = 2

    def needed(self, alive: int) -> int:
        if alive <= 0:
            return 1
        return max(min(self.min_count, alive), math.ceil(self.fraction * alive - 1e-9))

    def reached(self, support: int, alive: int) -> bool:
        return alive > 0 and support >= self.needed(alive)
