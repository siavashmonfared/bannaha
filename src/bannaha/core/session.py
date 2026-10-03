"""A session: one question, one fleet, one strategy.

The session owns the shared state (board, fetched documents), runs agent
turns through their harnesses, routes every tool call through the feature
hooks, and emits the event stream.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import events as ev
from .board import Board
from .errors import AgentDropped, FleetError, SessionStopped, TurnControl, TurnLimitReached
from .events import Event, EventBus
from .fleet import Agent, FleetEntry, build_agents
from .limits import Limits
from .outcome import Outcome
from .pricing import SEARCH_FEE_USD, Price, price_for
from .registry import harnesses
from .text import result_preview, summarize_args
from .tools import JsonDict, Tool, ToolContext, TurnState

if TYPE_CHECKING:
    from ..features.base import Feature
    from ..harnesses.base import Harness
    from ..strategies.base import Strategy
    from ..tools.toolbox import ToolOptions


@dataclass
class TurnJob:
    agent: Agent
    system: str
    prompt: str
    tools: list[Tool]
    phase: str = ""


@dataclass
class TurnResult:
    agent: str
    phase: str
    status: str  # finished | no_terminator | dropped | limit | budget | stopped | error
    terminal_tool: str | None = None
    payload: JsonDict | None = None
    text: str = ""
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    tools_used: list[str] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.status == "finished" and self.payload is not None


def check_fleet(entries: list[FleetEntry]) -> list[str]:
    registry = harnesses()
    problems: list[str] = []
    for e in entries:
        cls = registry.get(e.harness)
        if cls is None:
            problems.append(f"unknown harness {e.harness!r} (known: {', '.join(registry)})")
            continue
        for p in cls.check(e.backend, e.model):
            if p not in problems:
                problems.append(p)
    return problems


class Session:
    def __init__(self, *, question: str, fleet: list[FleetEntry], strategy: Strategy,
                 tool_options: ToolOptions, features: list[Feature] | None = None,
                 limits: Limits | None = None, bus: EventBus | None = None) -> None:
        from ..tools.documents import DocumentStore
        from ..tools.toolbox import Toolbox

        self.question = question.strip()
        self.fleet = fleet
        self.strategy = strategy
        self.agents = build_agents(fleet)
        if len(self.agents) < strategy.min_agents:
            raise FleetError(f"strategy {strategy.name!r} needs at least {strategy.min_agents} agents")
        strategy.assign_roles(self.agents)
        self.features = list(features or [])
        self.limits = limits or Limits()
        self.bus = bus or EventBus()
        self.board = Board()
        self.documents = DocumentStore()
        self.tool_options = tool_options
        self.toolbox = Toolbox(self, tool_options)
        self.round = 0
        self._harnesses: dict[str, Harness] = {}
        self._harness_lock = threading.Lock()
        self._stop = threading.Event()
        self._stop_reason = ""
        self._t0 = time.monotonic()
        self._tokens = 0
        self._tokens_lock = threading.Lock()
        self.prices: dict[str, Price | None] = {}
        self.search_price: Price | None = None
        self._prices_loaded = False
        self._cost_lock = threading.Lock()
        self._run_budget_hit = False

    # -- events -------------------------------------------------------------

    def emit(self, kind: str, agent: str | None = None, **data: Any) -> None:
        self.bus.emit(Event(kind=kind, agent=agent, round=self.round or None, data=data))

    # -- control ------------------------------------------------------------

    def cancel(self, reason: str = "cancelled") -> None:
        if not self._stop.is_set():
            self._stop_reason = reason
            self._stop.set()

    @property
    def stopped(self) -> bool:
        if not self._stop.is_set() and time.monotonic() - self._t0 > self.limits.max_wall_s:
            self.cancel("wall-time limit reached")
        return self._stop.is_set()

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    @property
    def total_tokens(self) -> int:
        return self._tokens

    # -- cost -----------------------------------------------------------------

    def load_prices(self) -> None:
        """Look up every model's list price once (OpenRouter's list, cached; see core.pricing)."""
        if self._prices_loaded:
            return
        self._prices_loaded = True
        for a in self.agents:
            if a.label not in self.prices:
                try:
                    self.prices[a.label] = price_for(a.backend, a.model)
                except Exception:  # pricing is best effort; a run never fails over it
                    self.prices[a.label] = None
        if self.tool_options.web:
            try:
                self.search_price = price_for("direct", self.tool_options.search_model)
            except Exception:
                self.search_price = None
        unknown = sorted({a.label for a in self.agents if self.prices.get(a.label) is None})
        if unknown and (self.limits.max_cost_usd or self.limits.max_agent_cost_usd):
            self.emit(ev.WARNING, message=f"no list price for {', '.join(unknown)}: their tokens do not count "
                                          "toward the cost limits (add them to prices.toml)")

    def agent_cost(self, agent: Agent) -> float:
        """Spend so far at list prices: model tokens (finished turns plus the live one) and web searches.
        A model with no known price counts as zero; `agent.stats.cost_usd` is None for it."""
        s = agent.stats
        p = self.prices.get(agent.label)
        model = p.cost(s.input_tokens + s.live_input_tokens, s.output_tokens + s.live_output_tokens) if p else 0.0
        search = SEARCH_FEE_USD * s.search_calls
        if self.search_price:
            search += self.search_price.cost(s.search_input_tokens, s.search_output_tokens)
        return model + search

    def total_cost(self) -> float:
        return sum(self.agent_cost(a) for a in self.agents)

    def _update_cost(self, agent: Agent) -> None:
        with self._cost_lock:
            cost = self.agent_cost(agent)
            agent.stats.cost_usd = cost if self.prices.get(agent.label) else None
            cap = self.limits.max_agent_cost_usd
            if cap and cost >= cap and not agent.over_budget:
                agent.over_budget = True
                self.emit(ev.BUDGET_REACHED, agent=agent.id, scope="agent", cost_usd=round(cost, 6), limit=cap)
            cap = self.limits.max_cost_usd
            if cap and not self._run_budget_hit:
                total = self.total_cost()
                if total >= cap:
                    self._run_budget_hit = True
                    self.emit(ev.BUDGET_REACHED, scope="run", cost_usd=round(total, 6), limit=cap)
                    self.cancel(f"cost limit reached (${total:.4f} of ${cap:g})")

    def _usage(self, agent: Agent) -> None:
        s = agent.stats
        self.emit(ev.USAGE, agent=agent.id, input_tokens=s.input_tokens + s.live_input_tokens,
                  output_tokens=s.output_tokens + s.live_output_tokens, search_calls=s.search_calls,
                  cost_usd=s.cost_usd, run_cost_usd=round(self.total_cost(), 6))

    def _on_usage(self, agent: Agent, input_tokens: int, output_tokens: int) -> None:
        agent.stats.live_input_tokens, agent.stats.live_output_tokens = input_tokens, output_tokens
        self._update_cost(agent)
        self._usage(agent)

    def alive(self, agents: list[Agent] | None = None) -> list[Agent]:
        return [a for a in (agents if agents is not None else self.agents) if a.alive]

    def tools(self, *names: str) -> list[Tool]:
        return self.toolbox.get(names)

    def start_round(self, number: int, phase: str, agents: list[Agent]) -> None:
        self.round = number
        self.emit(ev.ROUND_STARTED, phase=phase, agents=[a.id for a in agents])

    # -- turns --------------------------------------------------------------

    def _harness(self, agent: Agent) -> Harness:
        with self._harness_lock:
            if agent.harness not in self._harnesses:
                cls = harnesses().get(agent.harness)
                if cls is None:
                    raise FleetError(f"unknown harness {agent.harness!r}")
                self._harnesses[agent.harness] = cls()
            return self._harnesses[agent.harness]

    def run_turns(self, jobs: list[TurnJob]) -> list[TurnResult]:
        if not jobs:
            return []
        if len(jobs) == 1:
            return [self.run_turn(jobs[0])]
        with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="bannaha-turn") as pool:
            return list(pool.map(self.run_turn, jobs))

    def run_turn(self, job: TurnJob) -> TurnResult:
        from ..harnesses.base import TurnRequest

        agent = job.agent
        if not agent.alive:
            return TurnResult(agent=agent.id, phase=job.phase, status="dropped")
        if self.stopped:
            return TurnResult(agent=agent.id, phase=job.phase, status="stopped")
        if agent.over_budget:
            self.emit(ev.TURN_FINISHED, agent=agent.id, phase=job.phase, status="budget", tool_calls=0,
                      input_tokens=0, output_tokens=0, error=None, cost_usd=agent.stats.cost_usd)
            return TurnResult(agent=agent.id, phase=job.phase, status="budget")
        self.load_prices()
        turn = TurnState(phase=job.phase, terminals=tuple(t.name for t in job.tools if t.terminal),
                         max_tool_calls=self.limits.max_tool_calls_per_turn)
        ctx = ToolContext(session=self, agent=agent, turn=turn)
        agent.stats.turns += 1
        self.emit(ev.TURN_STARTED, agent=agent.id, phase=job.phase)
        req = TurnRequest(
            agent=agent, system=job.system, prompt=job.prompt, tools=job.tools,
            call_tool=lambda tool, args: self.invoke_tool(ctx, tool, args),
            is_done=lambda: turn.done or not agent.alive or self.stopped,
            max_steps=self.limits.max_steps_per_turn, max_output_tokens=self.limits.max_output_tokens,
            on_usage=lambda i, o: self._on_usage(agent, i, o),
        )
        status, error, reply = "finished", None, None
        try:
            reply = self._harness(agent).run_turn(req)
        except AgentDropped:
            status = "dropped"
        except TurnLimitReached:
            status = "limit"
        except SessionStopped:
            status = "stopped"
        except Exception as exc:  # harness/SDK failures end this turn, not the session
            status, error = "error", f"{type(exc).__name__}: {exc}"
        # SDKs sometimes wrap or swallow our control exceptions; trust state over exception type.
        if not agent.alive:
            status = "dropped"
        elif turn.done and status in ("error", "limit"):
            status, error = "finished", None
        elif status == "error" and self.stopped:
            status = "stopped"
        elif status == "finished" and turn.terminals and not turn.done:
            status = "no_terminator"
        if status == "limit" and agent.over_budget:
            status = "budget"
        # Harnesses that report usage live may stop short of their final tally (an aborted call); keep the larger.
        in_tok = max(reply.input_tokens if reply else 0, agent.stats.live_input_tokens)
        out_tok = max(reply.output_tokens if reply else 0, agent.stats.live_output_tokens)
        agent.stats.live_input_tokens = agent.stats.live_output_tokens = 0
        agent.stats.model_calls += reply.steps if reply else 0
        agent.stats.input_tokens += in_tok
        agent.stats.output_tokens += out_tok
        self._update_cost(agent)
        self._usage(agent)
        with self._tokens_lock:
            self._tokens += in_tok + out_tok
            if self.limits.max_total_tokens and self._tokens > self.limits.max_total_tokens:
                self.cancel("token budget reached")
        result = TurnResult(
            agent=agent.id, phase=job.phase, status=status, terminal_tool=turn.terminal_tool,
            payload=turn.terminal_args, text=(reply.text if reply else ""), tool_calls=turn.tool_calls,
            input_tokens=in_tok, output_tokens=out_tok, error=error, tools_used=list(turn.tools_used),
        )
        self.emit(ev.TURN_FINISHED, agent=agent.id, phase=job.phase, status=status,
                  tool_calls=turn.tool_calls, input_tokens=in_tok, output_tokens=out_tok, error=error,
                  cost_usd=agent.stats.cost_usd, text=(reply.text if reply else "")[:4000])
        return result

    def invoke_tool(self, ctx: ToolContext, tool: Tool, args: Any) -> JsonDict:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                return {"ok": False, "error": "tool arguments must be a JSON object"}
        if not isinstance(args, dict):
            args = {}
        if self.stopped:
            raise SessionStopped(self.stop_reason)
        for f in self.features:
            f.before_tool(ctx, tool, args)
        if not ctx.agent.alive:
            raise AgentDropped(ctx.agent.id)
        if ctx.agent.over_budget:
            raise TurnLimitReached(ctx.agent.id)
        if ctx.turn.done:
            return {"ok": False, "error": f"turn already finished with {ctx.turn.terminal_tool}"}
        if ctx.turn.tool_calls >= ctx.turn.max_tool_calls:
            raise TurnLimitReached(ctx.agent.id)
        ctx.turn.tool_calls += 1
        ctx.turn.tools_used.append(tool.name)
        ctx.agent.stats.tool_calls += 1
        if tool.name == "fetch_page":
            ctx.agent.stats.fetch_calls += 1
        elif tool.name == "run_python":
            ctx.agent.stats.python_calls += 1
        # `args` is a one-line summary for the live view; `full` keeps the whole call in the run log
        # (finishing tools carry the reasoning, reports and answers people want to read afterwards).
        self.emit(ev.TOOL_CALLED, agent=ctx.agent.id, tool=tool.name, args=summarize_args(args),
                  full=summarize_args(args, 20000 if tool.terminal else 4000))
        try:
            result = tool.handler(ctx, args)
        except TurnControl:
            raise
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("ok", True)
        if tool.terminal and result["ok"]:
            ctx.turn.terminal_tool = tool.name
            ctx.turn.terminal_args = dict(args)
        if tool.name == "web_search":
            self._update_cost(ctx.agent)
            self._usage(ctx.agent)
        for f in self.features:
            result = f.after_tool(ctx, tool, args, result)
        left = ctx.turn.max_tool_calls - ctx.turn.tool_calls
        if not ctx.turn.done and ctx.turn.terminals and left <= 2:
            result = {**result, "budget_note": f"{left} tool call(s) left this turn; "
                                               f"finish now with {' or '.join(ctx.turn.terminals)}."}
        self.emit(ev.TOOL_RETURNED, agent=ctx.agent.id, tool=tool.name, ok=bool(result.get("ok")),
                  error=result.get("error"), preview=result_preview(result))
        return result

    # -- run ----------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "strategy": self.strategy.name,
            "options": self.strategy.opts,
            "fleet": [{"id": a.id, "label": a.label, "role": a.role} for a in self.agents],
            "tools": self.tool_options.describe(),
            "features": [f.describe() for f in self.features],
            "limits": self.limits.describe(),
        }

    def run(self) -> Outcome:
        self._t0 = time.monotonic()
        self.emit(ev.SESSION_STARTED, **self.describe())
        self.load_prices()
        for f in self.features:
            f.on_session_start(self)
        try:
            outcome = self.strategy.run(self)
        except SessionStopped as exc:
            outcome = Outcome("stopped", detail={"reason": str(exc) or self.stop_reason})
        except Exception as exc:
            self.emit(ev.WARNING, message=f"strategy crashed: {type(exc).__name__}: {exc}")
            outcome = Outcome("error", detail={"error": f"{type(exc).__name__}: {exc}"})
        if self._stop.is_set() and outcome.status != "committed":
            outcome.status = "stopped"
            outcome.detail.setdefault("reason", self.stop_reason)
        self.emit(ev.SESSION_FINISHED, outcome=outcome.to_dict(), duration_s=round(self.elapsed, 2),
                  total_tokens=self._tokens, total_cost_usd=round(self.total_cost(), 6),
                  agents=[{"id": a.id, "alive": a.alive, "turns": a.stats.turns,
                           "tool_calls": a.stats.tool_calls,
                           "tokens": a.stats.input_tokens + a.stats.output_tokens,
                           "input_tokens": a.stats.input_tokens, "output_tokens": a.stats.output_tokens,
                           "search_calls": a.stats.search_calls,
                           "cost_usd": None if a.stats.cost_usd is None else round(a.stats.cost_usd, 6),
                           "over_budget": a.over_budget} for a in self.agents])
        return outcome
