"""Google Agent Development Kit harness.

Gemini models run natively; other models (OpenRouter, Bedrock) go through
ADK's LiteLLM wrapper. Each bannaha tool becomes an ADK `BaseTool` that
declares its full JSON Schema, so nested and nullable arguments survive.
The instruction is passed as a callable so ADK doesn't treat braces in a
prompt as state-template variables.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..backends import bedrock_problems, litellm_problems
from ..core.errors import TurnControl
from ..core.registry import register_harness
from .base import NUDGE, Harness, HarnessReply, TurnRequest

APP = "bannaha"


@register_harness
class ADKHarness(Harness):
    name = "adk"
    summary = "Google Agent Development Kit (Gemini natively; OpenRouter and Bedrock via LiteLLM)."
    backends = {"direct": [("GEMINI_API_KEY", "GOOGLE_API_KEY")], "openrouter": [("OPENROUTER_API_KEY",)],
                "bedrock": []}
    requires_module = "google.adk"
    extra = "adk"
    model_hints = {
        "direct": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
        "openrouter": ["anthropic/claude-haiku-4.5", "openai/gpt-5-mini", "qwen/qwen3-235b-a22b-2507"],
        "bedrock": ["us.anthropic.claude-haiku-4-5-20251001-v1:0"],
    }

    @classmethod
    def extra_problems(cls, backend: str, model: str) -> list[str]:
        problems = []
        if backend in ("openrouter", "bedrock"):
            problems += litellm_problems(cls.name, backend)
        if backend == "bedrock":
            problems += bedrock_problems()
        return problems

    def _model(self, req: TurnRequest) -> Any:
        if req.agent.backend == "openrouter":
            from google.adk.models.lite_llm import LiteLlm

            return LiteLlm(model=f"openrouter/{req.agent.model}")
        if req.agent.backend == "bedrock":
            from google.adk.models.lite_llm import LiteLlm

            return LiteLlm(model=f"bedrock/{req.agent.model}")
        return req.agent.model

    def run_turn(self, req: TurnRequest) -> HarnessReply:
        return asyncio.run(self._run(req))

    async def _run(self, req: TurnRequest) -> HarnessReply:
        from google.adk.agents import LlmAgent
        from google.adk.agents.run_config import RunConfig
        from google.adk.runners import InMemoryRunner
        from google.adk.tools import BaseTool
        from google.genai import types

        abort: list[BaseException] = []

        class Bridge(BaseTool):
            def __init__(self, tool) -> None:
                super().__init__(name=tool.name, description=tool.description)
                self.tool = tool

            def _get_declaration(self):
                return types.FunctionDeclaration(name=self.tool.name, description=self.tool.description,
                                                 parameters_json_schema=self.tool.parameters)

            async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
                try:
                    return await asyncio.to_thread(req.call_tool, self.tool, dict(args or {}))
                except TurnControl as exc:
                    abort.append(exc)
                    return {"ok": False, "error": "turn ended"}

        system = req.system
        agent = LlmAgent(
            name=req.agent.id, model=self._model(req), instruction=lambda _ctx: system,
            tools=[Bridge(t) for t in req.tools],
            generate_content_config=types.GenerateContentConfig(max_output_tokens=req.max_output_tokens),
        )
        runner = InMemoryRunner(agent=agent, app_name=APP)
        session = await runner.session_service.create_session(app_name=APP, user_id=req.agent.id)
        reply = HarnessReply()
        message = req.prompt
        for attempt in range(2):
            stream = runner.run_async(
                user_id=req.agent.id, session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=message)]),
                run_config=RunConfig(max_llm_calls=req.max_steps),
            )
            try:
                async for event in stream:
                    usage = getattr(event, "usage_metadata", None)
                    if usage is not None and not getattr(event, "partial", False):
                        reply.steps += 1
                        reply.input_tokens += getattr(usage, "prompt_token_count", 0) or 0
                        reply.output_tokens += (getattr(usage, "candidates_token_count", 0) or 0) + (
                            getattr(usage, "thoughts_token_count", 0) or 0)
                        req.report_usage(reply.input_tokens, reply.output_tokens)
                    content = getattr(event, "content", None)
                    texts = [p.text for p in (content.parts or []) if getattr(p, "text", None)] if content else []
                    if texts and event.is_final_response():
                        reply.text = "".join(texts)
                    if abort or req.is_done():
                        break
            finally:
                await stream.aclose()
            if abort:
                raise abort[0]
            if req.is_done() or not req.terminal_names or attempt:
                break
            message = NUDGE.format(names=", ".join(req.terminal_names))
        return reply
