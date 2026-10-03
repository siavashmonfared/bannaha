import asyncio
import json

import pytest

from bannaha import followup as fu
from bannaha.core.errors import BannahaError
from bannaha.core.pricing import Price
from bannaha.tools.toolbox import ToolOptions


def finished_run(make_session, tmp_path):
    s = make_session("vote", "mock@local:42*2,mock@local:41")
    s.run()
    return fu.RunMemory.from_events(s.events, tmp_path)


def test_parse_command():
    assert fu.parse_command("/rerun only count A") == ("/rerun", "only count A")
    assert fu.parse_command("why 42?") == (None, "why 42?")
    assert fu.parse_command("/HELP") == ("/help", "")


def test_strongest_is_the_priciest_model(monkeypatch):
    prices = {"claude-opus-4-6": 25e-6, "claude-haiku-4-5": 5e-6, "gpt-5.5": 30e-6}
    monkeypatch.setattr(fu, "price_for", lambda backend, model: Price(1e-6, prices[model], "test"))
    labels = ["anthropic@direct:claude-haiku-4-5", "anthropic@direct:claude-opus-4-6", "openai@direct:gpt-5.5"]
    assert fu.strongest(labels) == "openai@direct:gpt-5.5"


def test_memory_holds_the_run(make_session, tmp_path):
    mem = finished_run(make_session, tmp_path)
    ctx = mem.context()
    assert "What is the answer?" in ctx and "answer: 42" in ctx
    assert "final_answer" in ctx and len(mem.submissions) == 3
    assert mem.search("41")  # the dissenting answer can be found


def test_followup_conversation_is_saved_and_reloaded(make_session, tmp_path):
    mem = finished_run(make_session, tmp_path)
    chat = fu.FollowUp(mem)
    assert chat.agent == "mock@local:42" and chat.agent_id() == "a0"
    s = chat.session("why 42?", ToolOptions(web=False, python=False))
    out = s.run()
    assert out.status == "committed" and "why 42?" in out.answer
    chat.record("why 42?", out, s.total_cost())
    saved = [json.loads(x) for x in (tmp_path / "followups.jsonl").read_text().splitlines()]
    assert saved[0]["question"] == "why 42?" and (tmp_path / "followup_events.jsonl").is_file()
    again = fu.FollowUp(mem)
    assert again.history[0]["text"] == "why 42?"  # memory of the conversation survives
    assert chat.set_agent("a2") == "mock@local:41" and chat.agent_id() == "a2"
    assert chat.set_agent("mock@local:7") == "mock@local:7" and chat.agent_id() == "ask"
    with pytest.raises(BannahaError):
        chat.set_agent("nosuch@direct:x")


def test_rerun_question_carries_the_previous_answer(make_session, tmp_path):
    mem = finished_run(make_session, tmp_path)
    q = fu.rerun_question(mem, "only integers")
    assert "What is the answer?" in q and "answered: 42" in q and "only integers" in q


def test_tui_followup_and_help(tmp_path, monkeypatch):
    from textual.widgets import Input, RichLog

    from bannaha.cli.config import Settings
    from bannaha.tui.app import BannahaApp

    monkeypatch.setenv("BANNAHA_RUNS_DIR", str(tmp_path / "runs"))

    async def go():
        app = BannahaApp(start="run", settings=Settings(strategy="vote", fleet="mock@local:42*2", web=False,
                                                        banner=False))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen = app.screen
            ask = screen.query_one("#ask", Input)

            async def send(text):
                ask.focus()
                ask.value = text
                await pilot.press("enter")
                for _ in range(100):
                    await pilot.pause(0.05)
                    if screen.session is None:
                        break

            await send("What is the answer?")
            assert screen.chat is not None and "about this answer" in ask.placeholder
            await send("why is it 42?")
            assert screen.fu_done and screen.chat.history[-2]["text"] == "why is it 42?"
            await send("/help")
            lines = "\n".join(str(seg) for seg in screen.query_one("#log", RichLog).lines)
            assert "/rerun" in lines and "ctrl+o" in lines
        run_dirs = list((tmp_path / "runs").iterdir())
        assert (run_dirs[0] / "followups.jsonl").is_file()

    asyncio.run(go())
