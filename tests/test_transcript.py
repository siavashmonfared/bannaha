import json

from rich.console import Console

from bannaha.cli.themes import get_theme
from bannaha.cli.transcript import Transcript
from bannaha.core import events as ev
from bannaha.core.events import Event


def render(blocks) -> str:
    c = Console(width=100, record=True, color_system=None)
    for b in blocks:
        c.print(b)
    return c.export_text()


def run_events():
    fleet = [{"id": "a0", "label": "anthropic@direct:claude-opus-4-6", "role": "peer"},
             {"id": "a1", "label": "openai@direct:gpt-5", "role": "peer"}]
    long_out = "\n".join(f"line {i}" for i in range(20))
    return [
        Event(ev.SESSION_STARTED, data={"question": "What is the answer?", "strategy": "vote", "fleet": fleet,
                                        "tools": {"web": True, "python": True, "sandbox": "process"}}),
        Event(ev.ROUND_STARTED, round=1, data={"phase": "answer"}),
        Event(ev.TURN_STARTED, agent="a0", data={"phase": "answer"}),
        Event(ev.TURN_STARTED, agent="a1", data={"phase": "answer"}),
        Event(ev.TOOL_CALLED, agent="a0", data={"tool": "web_search",
                                                "full": json.dumps({"query": "meaning of life"})}),
        Event(ev.TOOL_CALLED, agent="a1", data={"tool": "run_python", "full": json.dumps({"code": "print(6*7)"})}),
        Event(ev.TOOL_RETURNED, agent="a1", data={"tool": "run_python", "ok": True, "preview": long_out}),
        Event(ev.TOOL_RETURNED, agent="a0", data={"tool": "web_search", "ok": True, "preview": "Douglas Adams: 42"}),
        Event(ev.TOOL_CALLED, agent="a0", data={"tool": "final_answer", "full": json.dumps(
            {"answer": "42", "confidence": 0.9, "rationale": "A long rationale that must appear in full. " * 4})}),
        Event(ev.TOOL_RETURNED, agent="a0", data={"tool": "final_answer", "ok": True, "preview": "ok"}),
        Event(ev.TURN_FINISHED, agent="a0", data={"status": "finished", "text": "", "cost_usd": 0.01}),
        Event(ev.ROUND_CLOSED, round=1, data={"phase": "answer", "decision": "commit",
                                              "clusters": [{"value": "42", "support": 2}]}),
    ]


def test_transcript_interleaves_and_keeps_answers_whole():
    tr = Transcript(get_theme("ocean"))
    out = render([b for e in run_events() for b in tr.feed(e)])
    assert out.index("a1") < out.index("⏺ run_python") < out.index("⏺ web_search")  # time order
    assert "line 3" in out and "line 4" not in out and "+16 lines (ctrl+o to expand)" in out
    flat = " ".join(out.split())
    assert flat.count("A long rationale that must appear in full.") == 4  # never clipped
    assert "Answer" in out and "confidence 90%" in out and "round 1 · answer" in out
    assert "⇢" in out


def test_transcript_full_and_focus():
    full = render([b for e in run_events() for b in Transcript(get_theme("ocean"), full=True).feed(e)])
    assert "line 19" in full and "ctrl+o" not in full
    tr = Transcript(get_theme("ocean"), focus="a0")
    focused = render([b for e in run_events() for b in tr.feed(e)])
    assert "web_search" in focused and "run_python" not in focused
    status = tr.status()
    assert "following a0" in status.plain
