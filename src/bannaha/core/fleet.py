"""Fleet specs: `harness@backend:model*count`, comma separated.

    anthropic@direct:claude-haiku-4-5*3,openai@openrouter:google/gemini-2.5-flash*2

Count defaults to 1. `×` is accepted in place of `*`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import FleetError


@dataclass
class AgentStats:
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    search_calls: int = 0
    search_input_tokens: int = 0
    search_output_tokens: int = 0
    model_calls: int = 0  # model requests, as the harness reports them
    fetch_calls: int = 0
    python_calls: int = 0
    live_input_tokens: int = 0  # the current turn, as the harness reports it (folded in when the turn ends)
    live_output_tokens: int = 0
    cost_usd: float | None = None  # at list prices; None when the model's price is unknown


@dataclass
class Agent:
    id: str
    harness: str
    backend: str
    model: str
    role: str = "peer"
    alive: bool = True
    over_budget: bool = False
    last_seen_version: int = 0
    stats: AgentStats = field(default_factory=AgentStats)

    @property
    def label(self) -> str:
        return f"{self.harness}@{self.backend}:{self.model}"


@dataclass(frozen=True)
class FleetEntry:
    harness: str
    backend: str
    model: str
    count: int = 1

    @property
    def label(self) -> str:
        return f"{self.harness}@{self.backend}:{self.model}"


def parse_fleet(spec: str) -> list[FleetEntry]:
    entries: list[FleetEntry] = []
    for raw in (spec or "").split(","):
        part = raw.strip().replace("×", "*")
        if not part:
            continue
        count = 1
        if "*" in part:
            part, _, n = part.rpartition("*")
            try:
                count = int(n)
            except ValueError as exc:
                raise FleetError(f"bad count in {raw.strip()!r}") from exc
            if count < 1:
                raise FleetError(f"count must be >= 1 in {raw.strip()!r}")
        if "@" not in part or ":" not in part.split("@", 1)[1]:
            raise FleetError(f"expected harness@backend:model[*count], got {raw.strip()!r}")
        harness, rest = part.split("@", 1)
        backend, model = rest.split(":", 1)
        if not (harness and backend and model):
            raise FleetError(f"expected harness@backend:model[*count], got {raw.strip()!r}")
        entries.append(FleetEntry(harness.strip().lower(), backend.strip().lower(), model.strip(), count))
    if not entries:
        raise FleetError("fleet is empty")
    return entries


def build_agents(entries: list[FleetEntry]) -> list[Agent]:
    agents: list[Agent] = []
    for e in entries:
        for _ in range(e.count):
            agents.append(Agent(id=f"a{len(agents)}", harness=e.harness, backend=e.backend, model=e.model))
    return agents


def fleet_summary(entries: list[FleetEntry]) -> str:
    return ", ".join(f"{e.label} ×{e.count}" for e in entries)
