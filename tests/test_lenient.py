"""Lenient tool-call handling for small local models: argument types repaired against the schema, and tool
calls written as text recovered (only when they name an offered tool and carry every required argument)."""
import pytest

from bannaha.core.tools import Tool, coerce_args, schema
from bannaha.harnesses.base import coerce_tool_call

PROPOSE = Tool("propose_angle", "", schema({"reason": {"type": "string"}, "direction": {"type": "string"},
                                            "step": {"type": "integer"}}, ["direction", "step"]),
               lambda c, a: {}, terminal=True)
VOTE = Tool("cast_vote", "", schema({"ranking": {"type": "array"}, "reason": {"type": "string"},
                                     "window_id": {"type": "integer"}}, ["ranking"]), lambda c, a: {}, terminal=True)
READ = Tool("read_board", "", schema({}), lambda c, a: {})


def test_coerce_args_repairs_types_from_the_schema():
    params = {"properties": {"n": {"type": "integer"}, "x": {"type": "number"}, "ok": {"type": "boolean"},
                             "ids": {"type": "array"}, "o": {"type": "object"}, "s": {"type": "string"}}}
    got = coerce_args(params, {"n": "5", "x": "0.7", "ok": "True", "ids": '["a0", "a1"]', "o": '{"k": 1}',
                               "s": "7", "extra": "1"})
    assert got == {"n": 5, "x": 0.7, "ok": True, "ids": ["a0", "a1"], "o": {"k": 1}, "s": "7", "extra": "1"}
    assert coerce_args(params, {"ids": "a0, a1"})["ids"] == ["a0", "a1"]
    assert coerce_args(params, {"n": "five"})["n"] == "five"  # left for the handler to reject


@pytest.mark.parametrize("text,tools,expected", [
    ('{"name": "cast_vote", "arguments": "{\\"ranking\\": [\\"a1\\"]}"}', [VOTE, READ],
     ("cast_vote", {"ranking": ["a1"]})),
    ('Calling now: {"name": "propose_angle", "parameters": {"direction": "reverse", "step": 5}}', [PROPOSE, READ],
     ("propose_angle", {"direction": "reverse", "step": 5})),
    ('propose_angle("E went up", "reverse", 5)', [PROPOSE, READ],
     ("propose_angle", {"reason": "E went up", "direction": "reverse", "step": 5})),
    ("propose_angle(direction='keep', step=2)", [PROPOSE], ("propose_angle", {"direction": "keep", "step": 2})),
    ("direction: decrease\nstep: 5", [PROPOSE], ("propose_angle", {"direction": "decrease", "step": 5})),
    ('```json\n{"direction": "increase", "step": 3}\n```', [PROPOSE],
     ("propose_angle", {"direction": "increase", "step": 3})),
])
def test_text_tool_calls_are_recovered(text, tools, expected):
    tool, args = coerce_tool_call(text, tools)
    assert (tool.name, args) == expected


@pytest.mark.parametrize("text,tools", [
    ("I think we should decrease the angle.", [PROPOSE]),
    ("direction: decrease", [PROPOSE]),  # step missing
    ('{"direction": "increase", "step": 3}', [PROPOSE, READ]),  # bare args are ambiguous with two tools
    ('{"name": "delete_everything", "arguments": {}}', [PROPOSE]),
    ("", [PROPOSE]),
])
def test_unclear_text_is_not_turned_into_a_call(text, tools):
    assert coerce_tool_call(text, tools) is None


def test_session_repairs_json_string_ranking(make_session):
    s = make_session("blackboard", "mock@local:42,mock@local:41")
    w = s.board.open_vote_window([{"id": "k1", "value": "42"}, {"id": "k2", "value": "41"}])
    from bannaha.core.tools import ToolContext, TurnState

    agent = s.agents[0]
    out = s.invoke_tool(ToolContext(s, agent, TurnState(terminals=("cast_vote",))), s.toolbox._tools["cast_vote"],
                        {"ranking": '["k2", "k1"]', "window_id": str(w.id)})
    assert out["ok"] and w.ballots[agent.id] == ["k2", "k1"]
