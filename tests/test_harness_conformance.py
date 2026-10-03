"""Every harness must deliver tool arguments to handlers unchanged (nested
objects, arrays, nulls), stop at the finishing tool, and let control signals
such as AgentDropped end the turn. Fake model clients, no network."""
import json
from types import SimpleNamespace

import pytest

from bannaha.core.errors import AgentDropped
from bannaha.core.fleet import Agent
from bannaha.core.tools import Tool, schema
from bannaha.harnesses.base import TurnRequest

NESTED = {"subtasks": [{"title": "Part 1", "instructions": "Find X", "weight": 2}], "note": None, "flag": True}


def make_request(agent: Agent, *, drop_on_first=False):
    calls, state = [], {"done": False}

    def echo(ctx, args):
        return {"ok": True, "echo": args}

    tools = [
        Tool("echo", "echo args", schema({"subtasks": {"type": "array", "items": {"type": "object"}},
                                         "note": {"type": ["string", "null"]}, "flag": {"type": "boolean"}}), echo),
        Tool("finish", "end the turn", schema({"answer": {"type": "string"}}, ["answer"]),
             lambda ctx, a: {"ok": True}, terminal=True),
    ]

    def call_tool(tool, args):
        if drop_on_first:
            raise AgentDropped(agent.id)
        calls.append((tool.name, args))
        if tool.terminal:
            state["done"] = True
        return tool.handler(None, args)

    req = TurnRequest(agent=agent, system="sys", prompt="go", tools=tools, call_tool=call_tool,
                      is_done=lambda: state["done"], max_steps=6, max_output_tokens=256)
    return req, calls


# -- Anthropic ----------------------------------------------------------------

class _Block(SimpleNamespace):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items() if v is not None}


class _FakeAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        assert kwargs["tools"][0]["input_schema"]["type"] == "object"
        if self.calls == 1:
            content = [_Block(type="text", text="thinking"),
                       _Block(type="tool_use", id="t1", name="echo", input=NESTED)]
        else:
            content = [_Block(type="tool_use", id="t2", name="finish", input={"answer": "42"})]
        return SimpleNamespace(content=content, usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def _anthropic():
    from bannaha.harnesses.anthropic_sdk import AnthropicHarness

    h = AnthropicHarness()
    fake = _FakeAnthropic()
    h._client = lambda backend: fake
    return h, fake


def test_anthropic_round_trips_args_and_stops_at_terminal():
    h, fake = _anthropic()
    req, calls = make_request(Agent("a0", "anthropic", "direct", "m"))
    reply = h.run_turn(req)
    assert calls == [("echo", NESTED), ("finish", {"answer": "42"})]
    assert fake.calls == 2 and reply.input_tokens == 20


def test_anthropic_propagates_agent_dropped():
    h, _ = _anthropic()
    req, _ = make_request(Agent("a0", "anthropic", "direct", "m"), drop_on_first=True)
    with pytest.raises(AgentDropped):
        h.run_turn(req)


# -- OpenAI Agents SDK ----------------------------------------------------------

def _openai():
    pytest.importorskip("agents")
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall

    from bannaha.harnesses.openai_agents import OpenAIAgentsHarness

    class FakeModel(Model):
        def __init__(self):
            self.calls = 0
            self.seen_tools = []

        async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs,
                               tracing, **kwargs):
            self.calls += 1
            self.seen_tools = tools
            name, args = ("echo", NESTED) if self.calls == 1 else ("finish", {"answer": "42"})
            item = ResponseFunctionToolCall(arguments=json.dumps(args), call_id=f"c{self.calls}", name=name,
                                            type="function_call", id=f"fc{self.calls}", status="completed")
            return ModelResponse(output=[item], usage=Usage(requests=1, input_tokens=7, output_tokens=3),
                                 response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError

    fake = FakeModel()
    h = OpenAIAgentsHarness()
    h._model = lambda req: fake
    return h, fake


def test_openai_round_trips_args_and_stops_at_terminal():
    h, fake = _openai()
    req, calls = make_request(Agent("a0", "openai", "direct", "m"))
    reply = h.run_turn(req)
    assert calls == [("echo", NESTED), ("finish", {"answer": "42"})]
    assert fake.calls == 2 and reply.input_tokens == 14


def test_openai_agent_dropped_ends_the_run():
    h, fake = _openai()
    req, _ = make_request(Agent("a0", "openai", "direct", "m"), drop_on_first=True)
    with pytest.raises(AgentDropped):
        h.run_turn(req)
    assert fake.calls == 1


# -- Google ADK -------------------------------------------------------------------

def _adk():
    pytest.importorskip("google.adk")
    from google.adk.models import BaseLlm, LlmResponse
    from google.genai import types

    from bannaha.harnesses.adk import ADKHarness

    class FakeGemini(BaseLlm):
        calls: int = 0
        declared: list = []

        async def generate_content_async(self, llm_request, stream=False):
            self.calls += 1
            decls = [d for t in (llm_request.config.tools or []) for d in (t.function_declarations or [])]
            self.declared = [(d.name, d.parameters_json_schema) for d in decls]
            name, args = ("echo", NESTED) if self.calls == 1 else ("finish", {"answer": "42"})
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                    name=name, args=args))]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=5,
                                                                          candidates_token_count=2))

    fake = FakeGemini(model="fake-gemini")
    h = ADKHarness()
    h._model = lambda req: fake
    return h, fake


def test_adk_round_trips_args_with_full_json_schema():
    h, fake = _adk()
    req, calls = make_request(Agent("a0", "adk", "direct", "m"))
    reply = h.run_turn(req)
    assert calls == [("echo", NESTED), ("finish", {"answer": "42"})]
    declared = dict(fake.declared)
    assert declared["echo"]["properties"]["subtasks"]["type"] == "array"
    assert reply.input_tokens == 10


def test_adk_agent_dropped_ends_the_run():
    h, fake = _adk()
    req, _ = make_request(Agent("a0", "adk", "direct", "m"), drop_on_first=True)
    with pytest.raises(AgentDropped):
        h.run_turn(req)
    assert fake.calls == 1


# -- banna ------------------------------------------------------------------------

def _banna(script):
    pytest.importorskip("banna_agent")
    from banna_agent.llm.base import ContentBlock, LLMReply, Usage

    from bannaha.harnesses.banna import BannaHarness

    class FakeBanna:
        provider = "fake"

        def __init__(self):
            self.calls = 0

        def chat(self, *, messages, tools=(), model=None, max_tokens=None, temperature=None, system=None,
                 extra=None):
            self.calls += 1
            kind, payload = script[min(self.calls, len(script)) - 1]
            if kind == "tool":
                name, args = payload
                content = [ContentBlock(kind="tool_use", id=f"t{self.calls}", name=name, arguments=args)]
                stop = "tool_use"
            else:
                content = [ContentBlock(kind="text", text=payload)]
                stop = "end_turn"
            return LLMReply(provider="fake", model="m", content=content, stop_reason=stop,
                            usage=Usage(tokens_in=4, tokens_out=1))

    fake = FakeBanna()
    h = BannaHarness()
    h._client = lambda req: (fake, "m")
    return h, fake


def test_banna_round_trips_args_and_stops_at_terminal():
    h, fake = _banna([("tool", ("echo", NESTED)), ("tool", ("finish", {"answer": "42"}))])
    req, calls = make_request(Agent("a0", "banna", "direct", "anthropic/m"))
    h.run_turn(req)
    assert calls == [("echo", NESTED), ("finish", {"answer": "42"})]
    assert fake.calls == 2


def test_banna_plain_final_answer_is_submitted_through_the_finishing_tool():
    h, _ = _banna([("text", "42")])
    req, calls = make_request(Agent("a0", "banna", "direct", "anthropic/m"))
    req.tools = [t for t in req.tools if t.name != "echo"]
    req.tools[0] = type(req.tools[0])("final_answer", "finish", req.tools[0].parameters,
                                      req.tools[0].handler, terminal=True)
    h.run_turn(req)
    assert calls == [("final_answer", {"answer": "42"})]


def test_banna_agent_dropped_ends_the_run():
    h, fake = _banna([("tool", ("echo", NESTED))] * 5)
    req, _ = make_request(Agent("a0", "banna", "direct", "anthropic/m"), drop_on_first=True)
    with pytest.raises(AgentDropped):
        h.run_turn(req)
    assert fake.calls == 1


def test_banna_surfaces_model_errors_instead_of_ending_silently():
    h, fake = _banna([("text", "unused")])

    def failing_chat(**kw):
        fake.calls += 1
        raise RuntimeError("Error code: 403 - model only available on agentic harnesses")

    fake.chat = failing_chat
    req, calls = make_request(Agent("a0", "banna", "openrouter", "x/y:free"))
    with pytest.raises(RuntimeError, match="403"):
        h.run_turn(req)
    assert calls == []


# -- Hermes Agent -----------------------------------------------------------------

@pytest.fixture
def fake_openai_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = {"chat_calls": 0, "tools_offered": []}
    script = [("echo", NESTED), ("finish", {"answer": "42"})]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self._json({"object": "list", "data": [{"id": "fake", "object": "model"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not self.path.endswith("/chat/completions"):
                return self._json({})
            state["chat_calls"] += 1
            state["tools_offered"] = [t["function"]["name"] for t in body.get("tools", [])]
            name, args = script[min(state["chat_calls"], len(script)) - 1]
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                    {"index": 0, "id": f"c{state['chat_calls']}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}]}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in chunks:
                c.update({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "fake"})
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def _json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1", state
    srv.shutdown()


def test_hermes_round_trips_args_and_only_offers_turn_tools(fake_openai_server, monkeypatch, tmp_path):
    from bannaha.harnesses.hermes import HermesHarness

    if not HermesHarness.available():
        pytest.skip("hermes-agent not installed")

    base_url, state = fake_openai_server
    monkeypatch.setenv("BANNAHA_HERMES_BASE_URL", base_url)
    monkeypatch.setenv("BANNAHA_HERMES_HOME", str(tmp_path / "hermes"))
    req, calls = make_request(Agent("a0", "hermes", "local", "fake"))
    reply = HermesHarness().run_turn(req)
    assert calls == [("echo", NESTED), ("finish", {"answer": "42"})]
    assert sorted(state["tools_offered"]) == ["echo", "finish"]
    assert state["chat_calls"] == 2 and reply.input_tokens == 18


def test_hermes_agent_dropped_ends_the_run(fake_openai_server, monkeypatch, tmp_path):
    from bannaha.harnesses.hermes import HermesHarness

    if not HermesHarness.available():
        pytest.skip("hermes-agent not installed")

    base_url, state = fake_openai_server
    monkeypatch.setenv("BANNAHA_HERMES_BASE_URL", base_url)
    monkeypatch.setenv("BANNAHA_HERMES_HOME", str(tmp_path / "hermes"))
    req, _ = make_request(Agent("a0", "hermes", "local", "fake"), drop_on_first=True)
    with pytest.raises(AgentDropped):
        HermesHarness().run_turn(req)
    assert state["chat_calls"] == 1


def test_openai_nudges_once_when_the_model_stops_without_finishing():
    pytest.importorskip("agents")
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    from bannaha.harnesses.openai_agents import OpenAIAgentsHarness

    class Lazy(Model):
        calls = 0

        async def get_response(self, *args, **kwargs):
            Lazy.calls += 1
            if Lazy.calls == 1:
                item = ResponseOutputMessage(id="m1", role="assistant", status="completed", type="message",
                                             content=[ResponseOutputText(text="I think it's 42.", type="output_text",
                                                                         annotations=[])])
            else:
                item = ResponseFunctionToolCall(arguments=json.dumps({"answer": "42"}), call_id="c2", name="finish",
                                                type="function_call", id="fc2", status="completed")
            return ModelResponse(output=[item], usage=Usage(requests=1, input_tokens=3, output_tokens=1),
                                 response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError

    h = OpenAIAgentsHarness()
    h._model = lambda req: Lazy()
    req, calls = make_request(Agent("a0", "openai", "direct", "m"))
    h.run_turn(req)
    assert calls == [("finish", {"answer": "42"})] and Lazy.calls == 2
