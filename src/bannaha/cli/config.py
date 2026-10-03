"""Saved defaults (~/.config/bannaha/config.toml) and .env loading."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import tomli_w

from ..tools.web_search import DEFAULT_SEARCH_MODEL


def config_dir() -> Path:
    base = os.environ.get("BANNAHA_CONFIG_DIR")
    if base:
        return Path(base).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "bannaha"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Settings:
    strategy: str = "blackboard"
    fleet: str = "mock@local:42*3,mock@local:41*2"
    web: bool = False
    python: bool = True
    sandbox: str = "process"
    peer_updates: bool = True
    search_model: str = DEFAULT_SEARCH_MODEL
    theme: str = "ocean"
    max_wall_s: float = 900.0
    max_calls: int = 16
    max_steps: int = 24
    max_output_tokens: int = 16384
    max_tokens: int = 0  # whole run; 0 = no limit
    max_cost_usd: float = 0.0  # whole run, at list prices; 0 = no limit
    max_agent_cost_usd: float = 0.0  # per agent; 0 = no limit
    banner: bool = True
    drop: float = 0.0
    drop_after: int = 2
    options: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_settings() -> tuple[Settings, bool]:
    path = config_path()
    if not path.exists():
        return Settings(), False
    data = tomllib.loads(path.read_text())
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known}), True


def save_settings(s: Settings) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(asdict(s)))
    return path


# Where each variable loaded by load_env_files came from (a file path); variables
# that were already in the environment are reported as "shell".
_ENV_SOURCES: dict[str, str] = {}


def env_files() -> list[Path]:
    return [Path.cwd() / ".env", config_dir() / ".env"]


def load_env_files(reload: bool = False) -> list[Path]:
    """Load KEY=VALUE pairs from ./.env and the config dir's .env without
    overriding variables that are already set. With reload=True, values that
    came from these files are re-read (so edits take effect); shell values stay."""
    if reload:
        for key, src in list(_ENV_SOURCES.items()):
            if src != "shell":
                os.environ.pop(key, None)
            del _ENV_SOURCES[key]
    loaded = []
    for path in env_files():
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            key, value = key.strip(), value.strip()
            if value[:1] in ("'", '"') and value[1:].find(value[0]) >= 0:
                value = value[1:1 + value[1:].find(value[0])]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            if not key or not value:
                continue
            if os.environ.get(key):
                _ENV_SOURCES.setdefault(key, "shell")
                continue
            os.environ[key] = value
            _ENV_SOURCES[key] = str(path)
        loaded.append(path)
    return loaded


def env_source(key: str) -> str | None:
    """'shell', the .env path it was loaded from, or None when not set."""
    if not os.environ.get(key):
        return None
    return _ENV_SOURCES.get(key, "shell")
