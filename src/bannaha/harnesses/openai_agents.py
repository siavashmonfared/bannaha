"""OpenAI Agents SDK harness: direct OpenAI, any model through OpenRouter, or
Bedrock models through the SDK's LiteLLM extension.

Tracing is disabled so runs are never uploaded to OpenAI's trace viewer.
The SDK turns tool exceptions into error text for the model; to make
control signals (agent dropped, turn limit, session stopped) actually end
the turn, the adapter cancels the run task and re-raises them.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from ..backends import OPENROUTER_BASE_URL, bedrock_problems, litellm_problems
from ..core.errors import TurnControl
from ..core.registry import register_harness
from ..core.text import to_json
from .base import NUDGE, Harness, HarnessReply, TurnRequest

MAX_RESULT_CHARS = 30_000


@register_harness
class OpenAIAgentsHarness(Harness):
    name = "openai"
    summary = "OpenAI Agents SDK (direct OpenAI, any model via OpenRouter, or Bedrock via LiteLLM)."
    backends = {"direct": [("OPENAI_API_KEY",)], "openrouter": [("OPENROUTER_API_KEY",)], "bedrock": []}
    requires_module = "agents"
    extra = "openai"
    model_hints = {
        "direct": ["gpt-5-mini", "gpt-5-nano", "gpt-5"],
        "openrouter": ["google/gemini-2.5-flash", "anthropic/claude-haiku-4.5", "openai/gpt-5-mini"],
        "bedrock": ["us.anthropic.claude-haiku-4-5-20251001-v1:0", "us.amazon.nova-pro-v1:0"],
    }

    @classmethod
    def extra_problems(cls, backend: str, model: str) -> list[str]:
        if backend == "bedrock":
            return litellm_problems(cls.name, backend) + bedrock_problems()
        return []

    def run_turn(self, req: TurnRequest) -> HarnessReply:
        return asyncio.run(self._run(req))

    def _model(self, req: TurnRequest):
        if req.agent.backend == "openrouter":
            from agents import OpenAIChatCompletionsModel
            from openai import AsyncOpenAI

            client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=os.environ["OPENROUTER_API_KEY"],
                                 default_headers={"X-Title": "bannaha"})
            return OpenAIChatCompletionsModel(model=req.agent.model, openai_client=client)
        if req.agent.backend == "bedrock":
            from agents.extensions.models.litellm_model import LitellmModel

            return LitellmModel(model=f"bedrock/{req.agent.model}")
        return req.agent.model

    async def _run(self, req: TurnRequest) -> HarnessReply:
        from agents import (
            Agent,
            FunctionTool,
            MaxTurnsExceeded,
            ModelSettings,
            RunHooks,
            Runner,
            StopAtTools,
            set_tracing_disabled,
        )

        set_tracing_disabled(True)
        abort: list[BaseException] = []
        task: asyncio.Task | None = None

        def wrap(tool) -> Any:
            async def invoke(_ctx: Any, args_json: str) -> str:
                try:
                    args = json.loads(args_json or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    out = await asyncio.to_thread(req.call_tool, tool, args)
                except TurnControl as exc:
                    abort.append(exc)
                    if task is not None:
                        task.cancel()
                    return to_json({"ok": False, "error": "turn ended"})
                return to_json(out, MAX_RESULT_CHARS)

            return FunctionTool(name=tool.name, description=tool.description, params_json_schema=tool.parameters,
                                on_invoke_tool=invoke, strict_json_schema=False)

        terminals = req.terminal_names
        agent = Agent(
            name=req.agent.id, instructions=req.system, tools=[wrap(t) for t in req.tools], model=self._model(req),
            model_settings=ModelSettings(max_tokens=req.max_output_tokens),
            tool_use_behavior=StopAtTools(stop_at_tool_names=terminals) if terminals else "run_llm_again",
        )
        reply = HarnessReply()
        seen = [0, 0]  # usage of the attempts that already finished

        class Usage(RunHooks):
            running = [0, 0]

            async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
                u = getattr(response, "usage", None)
                self.running[0] += getattr(u, "input_tokens", 0) or 0
                self.running[1] += getattr(u, "output_tokens", 0) or 0
                req.report_usage(seen[0] + self.running[0], seen[1] + self.running[1])

        turn_input: Any = req.prompt
        for attempt in range(2):
            hooks = Usage()
            hooks.running = [0, 0]
            task = asyncio.create_task(Runner.run(agent, turn_input, max_turns=req.max_steps, hooks=hooks))
            result = None
            try:
                result = await task
            except asyncio.CancelledError:
                pass
            except MaxTurnsExceeded:
                pass
            if abort:
                raise abort[0]
            if result is None:
                break
            usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
            reply.input_tokens += getattr(usage, "input_tokens", 0) or 0
            reply.output_tokens += getattr(usage, "output_tokens", 0) or 0
            reply.steps += getattr(usage, "requests", 0) or 0
            seen[:] = [reply.input_tokens, reply.output_tokens]
            reply.text = str(result.final_output or "")
            if req.is_done() or not terminals or attempt:
                break
            # The model stopped without calling a finishing tool: one nudge, with the conversation so far.
            turn_input = result.to_input_list() + [
                {"role": "user", "content": NUDGE.format(names=", ".join(terminals))}]
        return reply
