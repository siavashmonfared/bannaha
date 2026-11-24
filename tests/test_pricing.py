from bannaha.core.pricing import Price, load_overrides, openrouter_id, price_for

TABLE_RAW = [
    {"id": "anthropic/claude-haiku-4.5", "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
    {"id": "openai/gpt-5-mini", "pricing": {"prompt": "0.00000025", "completion": "0.000002"}},
    {"id": "google/gemini-2.5-flash", "pricing": {"prompt": "0.0000003", "completion": "0.0000025"}},
    {"id": "acme/router", "pricing": {"prompt": "-1", "completion": "-1"}},
    {"id": "acme/free:free", "pricing": {"prompt": "0", "completion": "0"}},
]


def table():
    from bannaha.core.pricing import _key

    out = {}
    for m in TABLE_RAW:
        out.setdefault(_key(m["id"]), m)
        out[m["id"].lower()] = m
    return out


def test_openrouter_id_mapping():
    assert openrouter_id("direct", "claude-haiku-4-5") == "anthropic/claude-haiku-4-5"
    assert openrouter_id("direct", "gpt-5-mini") == "openai/gpt-5-mini"
    assert openrouter_id("bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0") == \
        "anthropic/claude-haiku-4-5-20251001"
    assert openrouter_id("openrouter", "z-ai/glm-5.3-flash") == "z-ai/glm-5.3-flash"


def test_price_for_resolves_every_backend():
    t = table()
    haiku = price_for("direct", "claude-haiku-4-5", overrides={}, table=t)
    assert haiku and haiku.per_mtok() == (1.0, 5.0)
    assert price_for("bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0", overrides={}, table=t) == haiku
    assert price_for("direct", "gemini-2.5-flash", overrides={}, table=t).model_id == "google/gemini-2.5-flash"
    assert price_for("openrouter", "acme/free:free", overrides={}, table=t).cost(10**6, 10**6) == 0
    assert price_for("openrouter", "acme/router", overrides={}, table=t) is None  # variable price
    assert price_for("direct", "unknown-model", overrides={}, table=t) is None
    assert price_for("local", "42").source == "mock (fixed)"


def test_overrides_win(tmp_path):
    f = tmp_path / "prices.toml"
    f.write_text('["gpt-5-mini"]\ninput_per_mtok = 9.0\noutput_per_mtok = 10.0\n')
    ov = load_overrides(f)
    p = price_for("direct", "gpt-5-mini", overrides=ov, table=table())
    assert isinstance(p, Price) and p.per_mtok() == (9.0, 10.0)


def test_local_models_other_than_the_mock_are_free_and_overrides_still_win(tmp_path):
    assert price_for("local", "42", harness="mock").source == "mock (fixed)"
    p = price_for("local", "qwen2.5:3b", harness="banna", overrides={})
    assert p.cost(10**6, 10**6) == 0 and p.source.startswith("local")
    f = tmp_path / "prices.toml"
    f.write_text('["qwen2.5:3b"]\ninput_per_mtok = 0.1\noutput_per_mtok = 0.2\n')
    assert price_for("local", "qwen2.5:3b", harness="banna", overrides=load_overrides(f)).per_mtok() == (0.1, 0.2)
