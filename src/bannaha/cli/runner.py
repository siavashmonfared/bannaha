"""Turn saved settings plus per-run overrides into a ready Session.

Shared by `bannaha run` and the full-screen interface, so both check and
build runs the same way.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import BannahaError
from ..core.fleet import parse_fleet
from ..core.limits import Limits
from ..core.registry import strategies
from ..core.runlog import RunLog, new_run_dir
from ..core.session import Session, check_fleet
from ..features.dropout import Dropout
from ..features.peer_updates import PeerUpdates
from ..tools.toolbox import ToolOptions
from ..tools.web_search import search_key_present
from .config import Settings


@dataclass
class RunArgs:
    strategy: str | None = None
    fleet: str | None = None
    web: bool | None = None
    python: bool | None = None
    sandbox: str | None = None
    peer_updates: bool | None = None
    drop: float | None = None
    drop_after: int | None = None
    seed: int | None = None
    opts: list[str] = field(default_factory=list)
    max_wall: float | None = None
    max_calls: int | None = None
    max_tokens: int | None = None
    max_cost: float | None = None
    max_agent_cost: float | None = None
    search_model: str | None = None
    theme: str | None = None
    plain: bool = False
    dashboard: bool = False
    full: bool = False
    followup: bool = True
    json_out: bool = False
    runs_dir: Path | None = None
    save: bool = True


@dataclass
class Prepared:
    session: Session
    run_dir: Path | None


def parse_opts(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        if "=" not in item:
            raise BannahaError(f"--opt expects key=value, got {item!r}")
        k, _, v = item.partition("=")
        out[k.strip()] = v.strip()
    return out


def limits_for(s: Settings, a: RunArgs | None = None) -> Limits:
    """Saved limits (Setup page) with per-run flags on top; 0 means no limit for the run-wide caps."""
    a = a or RunArgs()
    return Limits(
        max_tool_calls_per_turn=a.max_calls or s.max_calls or Limits.max_tool_calls_per_turn,
        max_steps_per_turn=s.max_steps or Limits.max_steps_per_turn,
        max_output_tokens=s.max_output_tokens or Limits.max_output_tokens,
        max_wall_s=a.max_wall or s.max_wall_s or Limits.max_wall_s,
        max_total_tokens=(a.max_tokens if a.max_tokens is not None else s.max_tokens) or None,
        max_cost_usd=(a.max_cost if a.max_cost is not None else s.max_cost_usd) or None,
        max_agent_cost_usd=(a.max_agent_cost if a.max_agent_cost is not None else s.max_agent_cost_usd) or None,
    )


def problems_for(s: Settings, a: RunArgs | None = None) -> list[str]:
    """Everything that would stop a run with these settings, without building it."""
    a = a or RunArgs()
    name = a.strategy or s.strategy
    registry = strategies()
    if name not in registry:
        return [f"unknown strategy {name!r} (known: {', '.join(registry)})"]
    try:
        entries = parse_fleet(a.fleet or s.fleet)
        cls = registry[name]
        cls(**{**s.options.get(name, {}), **parse_opts(a.opts)})
    except BannahaError as exc:
        return [str(exc)]
    problems = check_fleet(entries)
    total = sum(e.count for e in entries)
    if total < cls.min_agents:
        problems.append(f"{name} needs at least {cls.min_agents} agents (fleet has {total})")
    if (s.web if a.web is None else a.web) and not search_key_present():
        problems.append("web search needs GEMINI_API_KEY or GOOGLE_API_KEY (or turn web search off)")
    return problems


def prepare(question: str, s: Settings, a: RunArgs | None = None) -> tuple[Prepared | None, list[str]]:
    a = a or RunArgs()
    problems = problems_for(s, a)
    if problems:
        return None, problems
    name = a.strategy or s.strategy
    entries = parse_fleet(a.fleet or s.fleet)
    strategy = strategies()[name](**{**s.options.get(name, {}), **parse_opts(a.opts)})
    features = []
    if (s.peer_updates if a.peer_updates is None else a.peer_updates) and strategy.uses_board:
        features.append(PeerUpdates())
    drop = s.drop if a.drop is None else a.drop
    if drop:
        after = s.drop_after if a.drop_after is None else a.drop_after
        features.append(Dropout(drop, after_calls=after, seed=a.seed))
    limits = limits_for(s, a)
    tool_options = ToolOptions(
        web=s.web if a.web is None else a.web, python=s.python if a.python is None else a.python,
        sandbox=a.sandbox or s.sandbox, search_model=a.search_model or s.search_model,
    )
    try:
        session = Session(question=question, fleet=entries, strategy=strategy, tool_options=tool_options,
                          features=features, limits=limits)
    except BannahaError as exc:
        return None, [str(exc)]
    run_dir = None
    if a.save:
        run_dir = new_run_dir(strategy.name, a.runs_dir)
        session.bus.subscribe(RunLog(run_dir))
    if any(e.harness == "mock" for e in entries) and not a.plain:
        os.environ.setdefault("BANNAHA_MOCK_DELAY", "0.25")
    return Prepared(session, run_dir), []
