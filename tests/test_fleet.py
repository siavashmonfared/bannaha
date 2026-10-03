import pytest

from bannaha.core.errors import FleetError
from bannaha.core.fleet import build_agents, parse_fleet
from bannaha.core.session import check_fleet


def test_parse_fleet_counts_and_models_with_colons():
    entries = parse_fleet("anthropic@direct:claude-haiku-4-5*3, openai@openrouter:meta-llama/llama-3:free ×2")
    assert [(e.harness, e.backend, e.model, e.count) for e in entries] == [
        ("anthropic", "direct", "claude-haiku-4-5", 3),
        ("openai", "openrouter", "meta-llama/llama-3:free", 2),
    ]
    assert [a.id for a in build_agents(entries)] == ["a0", "a1", "a2", "a3", "a4"]


@pytest.mark.parametrize("bad", ["", "anthropic", "anthropic@direct", "x@y:z*0", "x@y:z*n"])
def test_parse_fleet_rejects_bad_specs(bad):
    with pytest.raises(FleetError):
        parse_fleet(bad)


def test_check_fleet_reports_unknown_harness_backend_and_missing_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    problems = check_fleet(parse_fleet("nope@direct:m,anthropic@openrouter:m,anthropic@direct:m"))
    assert any("unknown harness 'nope'" in p for p in problems)
    assert any("does not support backend 'openrouter'" in p for p in problems)
    assert any("ANTHROPIC_API_KEY" in p for p in problems)
    assert check_fleet(parse_fleet("mock@local:42*2")) == []
