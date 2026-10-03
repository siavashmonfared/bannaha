import json

from typer.testing import CliRunner

from bannaha.cli.app import app

runner = CliRunner()


def test_version_and_list():
    assert runner.invoke(app, ["--version"]).stdout.startswith("bannaha ")
    out = runner.invoke(app, ["list"]).stdout
    assert "blackboard" in out and "vote" in out and "single" in out and "mock" in out
    assert "converge" not in out


def test_run_blackboard_plain_json(tmp_path):
    res = runner.invoke(app, ["run", "q?", "-s", "blackboard", "-f", "mock@local:42*2,mock@local:41*2,mock@local:40",
                              "--no-web", "--plain", "--json", "--runs-dir", str(tmp_path)])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout[res.stdout.index("\n{") + 1:])
    assert payload["answer"] == "42"
    run_dir = next(tmp_path.iterdir())
    assert (run_dir / "events.jsonl").exists() and (run_dir / "result.json").exists()


def test_run_with_dropout_and_options(tmp_path):
    res = runner.invoke(app, ["run", "q?", "-s", "blackboard", "-f", "mock@local:42*5", "--no-web", "--plain",
                              "--drop", "0.4", "--drop-after", "1", "--seed", "1", "-o", "rounds=2",
                              "--no-save"])
    assert res.exit_code == 0, res.stdout


def test_run_refuses_missing_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = runner.invoke(app, ["run", "q?", "-f", "anthropic@direct:claude-haiku-4-5*2", "--no-web", "--plain",
                              "--no-save"])
    assert res.exit_code == 2 and "ANTHROPIC_API_KEY" in res.stdout


def test_unknown_option_is_reported():
    res = runner.invoke(app, ["run", "q?", "-s", "vote", "-f", "mock@local:1*2", "-o", "nope=1", "--no-web",
                              "--plain", "--no-save"])
    assert res.exit_code == 2 and "unknown option" in res.stdout
