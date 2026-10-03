from bannaha.cli.config import Settings, load_env_files, load_settings, save_settings


def test_env_files_skip_empty_values_strip_comments_and_never_override(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("BANNAHA_CONFIG_DIR", str(cfg))
    monkeypatch.chdir(tmp_path)
    for k in ("BH_A", "BH_B", "BH_C", "BH_D"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BH_D", "from-shell")
    (tmp_path / ".env").write_text("BH_A=\nBH_B=local-b   # a comment\nexport BH_D=from-file\n")
    (cfg / ".env").write_text("BH_A=from-config\nBH_B=config-b\nBH_C='quoted # not a comment'\n")
    load_env_files()
    import os

    assert os.environ["BH_A"] == "from-config"
    assert os.environ["BH_B"] == "local-b"
    assert os.environ["BH_C"] == "quoted # not a comment"
    assert os.environ["BH_D"] == "from-shell"


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("BANNAHA_CONFIG_DIR", str(tmp_path))
    s = Settings(strategy="blackboard", fleet="mock@local:1*2", web=True, options={"blackboard": {"rounds": 4}})
    save_settings(s)
    loaded, existed = load_settings()
    assert existed and loaded == s
