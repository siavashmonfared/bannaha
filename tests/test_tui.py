"""Drive the full-screen app headlessly with Textual's pilot."""
import asyncio

from textual.widgets import DataTable, Input, Static

from bannaha.cli.config import Settings
from bannaha.tui.app import BannahaApp

MOCK = "mock@local:42*2,mock@local:41*2,mock@local:40"


def run(coro):
    return asyncio.run(coro)


def test_setup_toggles_and_start_requires_a_ready_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BANNAHA_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    async def go():
        app = BannahaApp(settings=Settings(strategy="blackboard", fleet=MOCK, web=False))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("right")  # strategy: blackboard -> single (alphabetical next)
            assert app.settings.strategy == "single"
            await pilot.press("left")
            assert app.settings.strategy == "blackboard"
            await pilot.press("down", "down", "right")  # web search on, but no key
            assert app.settings.web is True
            await pilot.press("s")
            assert app.current_mode == "setup", "must not start while web search has no key"
            await pilot.press("left")
            assert app.settings.web is False
            await pilot.press("s")
            await pilot.pause()
            assert app.current_mode == "run"

    run(go())


def test_run_page_runs_a_question_and_shows_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("BANNAHA_RUNS_DIR", str(tmp_path / "runs"))

    async def go():
        app = BannahaApp(start="run", settings=Settings(strategy="blackboard", fleet=MOCK, web=False))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen = app.screen
            ask = screen.query_one("#ask", Input)
            ask.focus()
            ask.value = "What is the answer?"
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause(0.05)
                if screen.session is None and screen.state and screen.state.final:
                    break
            assert screen.state.final["outcome"]["answer"] == "42"
            assert not ask.disabled
            assert screen.history == ["What is the answer?"]
            await pilot.press("up")
            assert ask.value == "What is the answer?"
        assert list((tmp_path / "runs").iterdir()), "the run is saved"

    run(go())


def test_fleet_page_edits_counts_and_removes_groups(tmp_path):
    async def go():
        app = BannahaApp(start="fleet", settings=Settings(strategy="vote", fleet="mock@local:42*2,mock@local:41"))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            assert table.row_count == 2
            await pilot.press("plus")
            assert app.settings.fleet == "mock@local:42*3,mock@local:41*1"
            await pilot.press("down", "x")
            assert app.settings.fleet == "mock@local:42*3"

    run(go())


def test_keys_page_marks_needed_and_missing_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")

    async def go():
        app = BannahaApp(start="keys", settings=Settings(fleet="anthropic@direct:claude-haiku-4-5*2"))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            rows = {str(table.get_row_at(i)[0]): table.get_row_at(i) for i in range(table.row_count)}
            assert str(rows["ANTHROPIC_API_KEY"][1]) == "yes" and "missing" in str(rows["ANTHROPIC_API_KEY"][2])
            assert "found" in str(rows["OPENROUTER_API_KEY"][2]) and str(rows["OPENROUTER_API_KEY"][3]) == "shell"
            rendered = " ".join(str(c) for r in rows.values() for c in r)
            assert "sk-test-not-real" not in rendered, "key values are never displayed"

    run(go())


def test_runs_page_lists_and_replays_saved_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("BANNAHA_RUNS_DIR", str(tmp_path / "runs"))
    from bannaha.cli.runner import prepare

    prepared, problems = prepare("q?", Settings(strategy="vote", fleet="mock@local:42*3", web=False))
    assert not problems
    prepared.session.run()

    async def go():
        app = BannahaApp(start="runs", settings=Settings())
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            assert table.row_count == 1 and "committed" in str(table.get_row_at(0)[3])
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "RunDetailScreen"
            replay = str(app.screen.query_one("#replay", Static).render())
            assert replay
            await pilot.press("escape")
            assert type(app.screen).__name__ == "RunsScreen"

    run(go())


def test_page_keys_switch_pages_but_type_into_the_question_box():
    async def go():
        app = BannahaApp(start="setup", settings=Settings(fleet=MOCK, web=False))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            for key, mode in (("2", "fleet"), ("3", "keys"), ("5", "runs"), ("4", "run")):
                await pilot.press(key)
                await pilot.pause()
                assert app.current_mode == mode
            ask = app.screen.query_one("#ask", Input)
            ask.focus()
            await pilot.press("1", "2")
            assert app.current_mode == "run" and ask.value == "12"

    run(go())


def test_clicking_the_page_bar_switches_pages():
    from bannaha.tui.widgets import PageBar

    async def go():
        app = BannahaApp(start="fleet", settings=Settings(fleet=MOCK, web=False))
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            bar = app.screen.query_one(PageBar)
            start, end, key = next(s for s in bar._segments() if s[2] == "keys")
            await pilot.click(PageBar, offset=(start + 1, 0))
            await pilot.pause()
            assert app.current_mode == "keys"

    run(go())


def test_limits_flow_from_settings_into_the_run():
    from bannaha.cli.runner import RunArgs, limits_for

    s = Settings(max_calls=5, max_cost_usd=0.5, max_tokens=0)
    lim = limits_for(s)
    assert (lim.max_tool_calls_per_turn, lim.max_cost_usd, lim.max_total_tokens) == (5, 0.5, None)
    lim = limits_for(s, RunArgs(max_calls=9, max_cost=0))
    assert (lim.max_tool_calls_per_turn, lim.max_cost_usd) == (9, None)


def test_setup_has_limits_row_and_banner_toggle(tmp_path):
    from bannaha.tui.banner import Banner

    async def go():
        app = BannahaApp(settings=Settings(strategy="vote", fleet=MOCK, web=False))
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            rows = app.screen.query_one("#rows")
            ids = [rows.get_option_at_index(i).id for i in range(rows.option_count)]
            assert "limits" in ids and "banner" in ids
            banner = app.screen.query_one(Banner)
            await pilot.pause(0.2)
            assert banner.display
            await pilot.press("ctrl+b")
            assert app.settings.banner is False and not banner.display

    run(go())


def test_activity_panel_shows_recent_steps():
    from rich.console import Console

    from bannaha.cli.live import LiveState
    from bannaha.cli.themes import get_theme
    from bannaha.core.fleet import parse_fleet
    from bannaha.core.registry import strategies
    from bannaha.core.session import Session
    from bannaha.tools.toolbox import ToolOptions

    s = Session(question="Q?", fleet=parse_fleet("mock@local:42*3"), strategy=strategies()["blackboard"](),
                tool_options=ToolOptions(web=False, python=False))
    st = LiveState(get_theme("ocean"))
    s.bus.subscribe(st.apply)
    s.run()
    console = Console(width=120, record=True)
    console.print(st.activity_panel())
    out = console.export_text()
    assert "read_board" in out and "propose_answer" in out
