from dataclasses import asdict, dataclass, field
from typing import Any

# committed: the strategy settled on an answer.
# no_answer: agents ran but nothing usable came back.
# failed: the strategy's rule could not be met (e.g. no quorum, fallback=fail).
# stopped: cancelled, or a wall-time / token budget ran out.
# error: something crashed.
STATUSES = ("committed", "no_answer", "failed", "stopped", "error")


@dataclass
class Outcome:
    status: str
    answer: str | None = None
    unit: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
