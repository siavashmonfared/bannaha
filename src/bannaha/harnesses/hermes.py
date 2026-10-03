"""Nous Research Hermes Agent harness.

bannaha tools are registered in-process in Hermes's tool registry, one
toolset per tool, and only the current turn's tools are enabled, so none of
Hermes's own terminal/browser/file tools are offered. Hermes passes a
`task_id` to every tool handler, which routes each call to the right agent's
turn. Hermes catches tool exceptions itself, so finishing tools and control
signals end the turn with `interrupt()`.

Hermes keeps state (sessions, memories, skills) in its home directory; bannaha
gives it a private one (BANNAHA_HERMES_HOME, default ~/.cache/bannaha/hermes)
and turns memory, context files and persona loading off.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..backends import OPENAI_BASE_URL, OPENROUTER_BASE_URL
from ..core.errors import TurnControl
from ..core.registry import register_harness
from ..core.text import to_json
from .base import NUDGE, Harness, HarnessReply, TurnRequest

MAX_RESULT_CHARS = 30_000
TOOLSET_PREFIX = "bannaha_"


@dataclass
class _Route:
    req: TurnRequest
    tools: dict[str, Any]
    abort: list[BaseException] = field(default_factory=list)
    agent: Any = None


_routes: dict[str, _Route] = {}
_routes_lock = threading.Lock()
_import_lock = threading.Lock()


def _hermes_home() -> Path:
    return Path(os.environ.get("BANNAHA_HERMES_HOME") or "~/.cache/bannaha/hermes").expanduser()


# Hermes resolves its home (logs, state.db) when it is first imported. Point it
# at bannaha's private home now, before anything can import it, so a user's own
# ~/.hermes is never read or written.
os.environ["HERMES_HOME"] = str(_hermes_home())


def _import_hermes():
    with _import_lock:
        home = _hermes_home()
        home.mkdir(parents=True, exist_ok=True)
        os.environ["HERMES_HOME"] = str(home)
        import run_agent
        from tools.registry import registry

        return run_agent, registry


def _router(name: str):
    def handler(args: dict[str, Any], task_id: str | None = None, **_: Any) -> str:
        with _routes_lock:
            route = _routes.get(task_id or "")
        if route is None:
            return json.dumps({"ok": False, "error": "no active bannaha turn for this call"})
        tool = route.tools.get(name)
        if tool is None:
            return json.dumps({"ok": False, "error": f"tool {name!r} is not enabled this turn"})
        try:
            out = route.req.call_tool(tool, dict(args or {}))
        except TurnControl as exc:
            route.abort.append(exc)
            route.agent.interrupt()
            return json.dumps({"ok": False, "error": "turn ended"})
        if route.req.is_done():
            route.agent.interrupt()
        return to_json(out, MAX_RESULT_CHARS)

    return handler


@register_harness
class HermesHarness(Harness):
    name = "hermes"
    summary = "Nous Research Hermes Agent, with bannaha tools registered in-process."
    backends = {"openrouter": [("OPENROUTER_API_KEY",)], "direct": [("OPENAI_API_KEY",)],
                "local": [("BANNAHA_HERMES_BASE_URL",)]}
    requires_module = "run_agent"
    extra = "hermes"
    model_hints = {
        "openrouter": ["google/gemini-2.5-flash", "qwen/qwen3-235b-a22b-2507", "deepseek/deepseek-chat-v3.1"],
        "direct": ["gpt-5-mini"],
        "local": ["hermes-4-70b"],
    }

    def _endpoint(self, backend: str) -> tuple[str, str]:
        if backend == "openrouter":
            return OPENROUTER_BASE_URL, os.environ["OPENROUTER_API_KEY"]
        if backend == "local":
            return os.environ["BANNAHA_HERMES_BASE_URL"], os.environ.get("BANNAHA_HERMES_API_KEY", "not-needed")
        return OPENAI_BASE_URL, os.environ["OPENAI_API_KEY"]

    def run_turn(self, req: TurnRequest) -> HarnessReply:
        run_agent, registry = _import_hermes()
        for t in req.tools:
            registry.register(name=t.name, toolset=TOOLSET_PREFIX + t.name,
                              schema={"name": t.name, "description": t.description, "parameters": t.parameters},
                              handler=_router(t.name), description=t.description, override=True)
        base_url, api_key = self._endpoint(req.agent.backend)
        task_id = f"bannaha-{req.agent.id}-{uuid.uuid4().hex[:10]}"
        route = _Route(req=req, tools={t.name: t for t in req.tools})
        agent = run_agent.AIAgent(
            base_url=base_url, api_key=api_key, model=req.agent.model, max_iterations=req.max_steps,
            enabled_toolsets=[TOOLSET_PREFIX + t.name for t in req.tools], quiet_mode=True,
            skip_context_files=True, skip_memory=True, load_soul_identity=False, save_trajectories=False,
            checkpoints_enabled=False, max_tokens=req.max_output_tokens,
        )
        route.agent = agent
        with _routes_lock:
            _routes[task_id] = route
        reply = HarnessReply()
        try:
            message, history = req.prompt, None
            for attempt in range(2):
                result = agent.run_conversation(message, system_message=req.system,
                                                conversation_history=history, task_id=task_id) or {}
                reply.steps += int(result.get("api_calls") or 0)
                reply.input_tokens += int(result.get("input_tokens") or 0)
                reply.output_tokens += int(result.get("output_tokens") or 0)
                if result.get("final_response"):
                    reply.text = str(result["final_response"])
                if route.abort:
                    raise route.abort[0]
                if result.get("failed") and not req.is_done():
                    raise RuntimeError(f"hermes: {result.get('error') or 'run failed'}")
                if req.is_done() or not req.terminal_names or attempt:
                    break
                message, history = NUDGE.format(names=", ".join(req.terminal_names)), result.get("messages")
        finally:
            with _routes_lock:
                _routes.pop(task_id, None)
            close = getattr(agent, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # best-effort cleanup of an SDK client
                    pass
        return reply
