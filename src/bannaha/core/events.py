"""The event stream every strategy, tool and harness writes to.

The live view and the JSONL run log are both just subscribers, so a new
strategy or tool never needs UI changes: emit events and they show up.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

SESSION_STARTED = "session_started"
SESSION_FINISHED = "session_finished"
ROUND_STARTED = "round_started"
ROUND_CLOSED = "round_closed"
TURN_STARTED = "turn_started"
TURN_FINISHED = "turn_finished"
TOOL_CALLED = "tool_called"
TOOL_RETURNED = "tool_returned"
CLAIM_POSTED = "claim_posted"
CLAIM_ENDORSED = "claim_endorsed"
CLAIM_CHALLENGED = "claim_challenged"
CLAIM_CORROBORATED = "claim_corroborated"
MESSAGE_SENT = "message_sent"
PROPOSAL_POSTED = "proposal_posted"
VOTE_WINDOW_OPENED = "vote_window_opened"
VOTE_CAST = "vote_cast"
AGENT_DROPPED = "agent_dropped"
ANSWER_SUBMITTED = "answer_submitted"
USAGE = "usage"
BUDGET_REACHED = "budget_reached"
WARNING = "warning"


@dataclass
class Event:
    kind: str
    agent: str | None = None
    round: int | None = None
    data: dict[str, Any] = field(default_factory=dict)
    t: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subs.append(fn)

    def emit(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            fn(event)
