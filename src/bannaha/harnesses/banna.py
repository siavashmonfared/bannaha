"""banna harness: banna's ReAct policy loop over bannaha tools.

Backends:
  direct      model is `vendor/model` with vendor anthropic, openai or gemini
  openrouter  any OpenRouter model (banna's OpenAI-compatible client)
  bedrock     a Bedrock model id

banna's loop catches `Exception` from tools, so control signals and the
finishing tool end the turn by raising a `BaseException` subclass. banna
also has its own "final answer" step; if a model ends that way instead of
calling the turn's finishing tool, the answer is submitted through that tool.
"""
from __future__ import annotations

import os
from typing import Any

from ..backends import OPENROUTER_BASE_URL, bedrock_problems
from ..core.errors import TurnControl
from ..core.registry import register_harness
from .base import Harness, HarnessReply, TurnRequest

VENDORS = {"anthropic": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
           "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
FINISH_NOTE = ("\n\nWhen you are done, call the {name} tool. If you answer in plain text instead, your answer "
               "is submitted through {name} for you.")


class _EndTurn(BaseException):
    """Unwinds banna's loop (which catches Exception) at the end of a turn."""


def _split_vendor(model: str) -> tuple[str, str]:
    vendor, _, name = model.partition("/")
    return vendor.lower(), name


def _finish_args(tool_name: str, text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    return {
        "final_answer": {"answer": text},
        "propose_answer": {"value": text, "rationale": "final answer from the agent's own loop"},
        "reply": {"answer": text},
    }.get(tool_name)


@register_harness
class BannaHarness(Harness):
    name = "banna"
    summary = "banna's ReAct policy (direct Anthropic/OpenAI/Gemini, OpenRouter or Bedrock)."
    backends = {"direct": [], "openrouter": [("OPENROUTER_API_KEY",)], "bedrock": []}
    requires_module = "banna_agent"
    extra = "banna"
    model_hints = {
        "direct": ["anthropic/claude-haiku-4-5", "openai/gpt-5-mini", "gemini/gemini-2.5-flash"],
        "openrouter": ["google/gemini-2.5-flash", "anthropic/claude-haiku-4.5"],
        "bedrock": ["us.anthropic.claude-haiku-4-5-20251001-v1:0"],
    }

    @classmethod
    def requirements(cls, backend: str, model: str) -> list[tuple[str, ...]]:
        if backend == "direct":
            vendor, _ = _split_vendor(model)
            return [VENDORS[vendor]] if vendor in VENDORS else []
        return super().requirements(backend, model)

    @classmethod
    def extra_problems(cls, backend: str, model: str) -> list[str]:
        if backend == "direct":
            vendor, name = _split_vendor(model)
            if vendor not in VENDORS or not name:
                return [f"banna@direct expects vendor/model with vendor in {', '.join(VENDORS)}, got {model!r}"]
        if backend == "bedrock":
            return bedrock_problems()
        return []

    def _client(self, req: TurnRequest) -> tuple[Any, str]:
        from banna_agent.llm import registry as llm

        backend, model = req.agent.backend, req.agent.model
        if backend == "openrouter":
            cfg = llm.OpenAIConfig(api_key=os.environ["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL)
            return llm.make_client("openai", model=model, env_override=cfg), model
        if backend == "bedrock":
            return llm.make_client("bedrock", model=model), model
        vendor, name = _split_vendor(model)
        if vendor == "gemini":
            cfg = llm.GeminiConfig(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
            return llm.make_client("gemini", model=name, env_override=cfg), name
        return llm.make_client(vendor, model=name), name

    def run_turn(self, req: TurnRequest) -> HarnessReply:
        from banna_agent.core.agent import run_policy
        from banna_agent.core.state import AgentState
        from banna_agent.core.types import Budget
        from banna_agent.policies.react import ReActPolicy
        from banna_agent.tools.base import JsonTool, ToolRegistry

        abort: list[BaseException] = []

        def wrap(tool):
            def handler(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    out = req.call_tool(tool, dict(args or {}))
                except TurnControl as exc:
                    abort.append(exc)
                    raise _EndTurn() from exc
                if req.is_done():
                    raise _EndTurn()
                return out
            return JsonTool(name=tool.name, description=tool.description, input_schema=tool.parameters,
                            handler=handler)

        llm_client, model = self._client(req)
        terminals = req.terminal_names
        system = req.system + (FINISH_NOTE.format(name=terminals[0]) if len(terminals) == 1 else "")
        policy = ReActPolicy(model=model, system_prompt=system, max_tokens=req.max_output_tokens)
        state = AgentState(question=req.prompt, budget=Budget(max_steps=req.max_steps, max_wall_s=3600.0))
        tools = ToolRegistry([wrap(t) for t in req.tools])
        try:
            state = run_policy(state, policy, llm=llm_client, tools=tools)
        except _EndTurn:
            pass
        if abort:
            raise abort[0]
        if not req.is_done():
            # banna records a failed model call as a step instead of raising; surface it so the turn
            # shows the real reason (auth, rate limit, unsupported model) rather than a silent no-op.
            errors = [s.action.text for s in state.trace.steps
                      if (s.action.meta or {}).get("error") and str(s.action.text or "").startswith("[llm_error]")]
            if errors and not state.trace.final_answer:
                raise RuntimeError(errors[-1].removeprefix("[llm_error] ")[:500])
        final = state.trace.final_answer or ""
        if not req.is_done() and len(terminals) == 1:
            args = _finish_args(terminals[0], final)
            if args is not None:
                tool = next(t for t in req.tools if t.name == terminals[0])
                req.call_tool(tool, args)
        b = state.budget
        return HarnessReply(text=final, input_tokens=b.tokens_in, output_tokens=b.tokens_out, steps=b.steps_used)
