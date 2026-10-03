"""Anthropic SDK harness: a plain Messages API tool-use loop."""
from __future__ import annotations

from typing import Any

from ..backends import aws_region, bedrock_problems
from ..core.registry import register_harness
from ..core.text import to_json
from .base import NUDGE, Harness, HarnessReply, TurnRequest

MAX_RESULT_CHARS = 30_000


def to_anthropic_tools(tools) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


@register_harness
class AnthropicHarness(Harness):
    name = "anthropic"
    summary = "Anthropic Python SDK, Messages API tool-use loop (direct or Bedrock)."
    backends = {"direct": [("ANTHROPIC_API_KEY",)], "bedrock": []}
    requires_module = "anthropic"
    extra = "anthropic"
    model_hints = {
        "direct": ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5-5"],
        "bedrock": ["us.anthropic.claude-haiku-4-5-20251001-v1:0", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"],
    }

    @classmethod
    def extra_problems(cls, backend: str, model: str) -> list[str]:
        return bedrock_problems() if backend == "bedrock" else []

    def _client(self, backend: str):
        import anthropic

        if backend == "bedrock":
            return anthropic.AnthropicBedrock(aws_region=aws_region())
        return anthropic.Anthropic()

    def run_turn(self, req: TurnRequest) -> HarnessReply:
        client = self._client(req.agent.backend)
        tools = to_anthropic_tools(req.tools)
        by_name = {t.name: t for t in req.tools}
        messages: list[dict[str, Any]] = [{"role": "user", "content": req.prompt}]
        reply = HarnessReply()
        nudged = False
        for _ in range(req.max_steps):
            resp = client.messages.create(model=req.agent.model, max_tokens=req.max_output_tokens,
                                          system=req.system, tools=tools, messages=messages)
            reply.steps += 1
            reply.input_tokens += getattr(resp.usage, "input_tokens", 0) or 0
            reply.output_tokens += getattr(resp.usage, "output_tokens", 0) or 0
            req.report_usage(reply.input_tokens, reply.output_tokens)
            messages.append({"role": "assistant",
                             "content": [b.model_dump(mode="json", exclude_none=True) for b in resp.content]})
            text = "".join(b.text for b in resp.content if b.type == "text")
            if text:
                reply.text = text
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                if req.is_done() or not req.terminal_names or nudged:
                    break
                nudged = True
                messages.append({"role": "user", "content": NUDGE.format(names=", ".join(req.terminal_names))})
                continue
            results = []
            for u in uses:
                tool = by_name.get(u.name)
                out = req.call_tool(tool, u.input) if tool else {"ok": False, "error": f"unknown tool {u.name!r}"}
                results.append({"type": "tool_result", "tool_use_id": u.id,
                                "content": to_json(out, MAX_RESULT_CHARS), "is_error": not out.get("ok", True)})
            messages.append({"role": "user", "content": results})
            if req.is_done():
                break
        return reply
