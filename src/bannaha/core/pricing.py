"""Per-token model prices, for settling what a run actually cost.

Prices come from, in order:
  1. ~/.config/bannaha/prices.toml, for anything you want to pin by hand:
         ["gpt-5-mini"]
         input_per_mtok = 0.25
         output_per_mtok = 2.0
  2. OpenRouter's public model list (https://openrouter.ai/api/v1/models),
     cached for a day. It lists OpenAI, Anthropic and Google models too, so
     it doubles as a list-price reference for direct and Bedrock runs.
Prices are list prices for uncached input; prompt caching, batch discounts
and long-context surcharges are not modeled.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
PRICE_LINKS = {
    "OpenRouter (every model it serves, JSON; per-model: /api/v1/models/<author>/<slug>/endpoints)":
        OPENROUTER_MODELS,
    "OpenAI": "https://platform.openai.com/docs/pricing",
    "Anthropic": "https://docs.claude.com/en/docs/about-claude/pricing",
    "Google Gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "AWS Bedrock": "https://aws.amazon.com/bedrock/pricing/",
}
CACHE_TTL_S = 24 * 3600
# Gemini grounding with Google Search, per grounded request beyond the free tier (Gemini 2.5 models).
SEARCH_FEE_USD = 0.035
_VENDORS = (("claude", "anthropic"), ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
            ("gemini", "google"), ("gemma", "google"))
_lock = threading.Lock()
_table: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class Price:
    input_per_tok: float
    output_per_tok: float
    source: str
    model_id: str = ""

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * self.input_per_tok + output_tokens * self.output_per_tok

    def per_mtok(self) -> tuple[float, float]:
        return self.input_per_tok * 1e6, self.output_per_tok * 1e6


def _config_dir() -> Path:
    base = os.environ.get("BANNAHA_CONFIG_DIR")
    if base:
        return Path(base).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "bannaha"


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(base).expanduser() / "bannaha" / "openrouter_models.json"


def openrouter_id(backend: str, model: str) -> str:
    """Best guess at the OpenRouter id for a model reached through any backend."""
    if backend == "openrouter":
        return model
    m = model.lower()
    if backend == "bedrock":
        m = re.sub(r"^(us|eu|apac|global|us-gov)\.", "", m)
        m = re.sub(r"-v\d+(:\d+)?$", "", m)
        if "." in m.split("-", 1)[0]:
            vendor, _, rest = m.partition(".")
            return f"{vendor}/{rest}"
    for prefix, vendor in _VENDORS:
        if m.startswith(prefix):
            return f"{vendor}/{m}"
    return m


def _key(model_id: str) -> str:
    k = model_id.lower().split(":", 1)[0] if not model_id.lower().endswith(":free") else model_id.lower()
    k = re.sub(r"-\d{8}$", "", k)
    k = re.sub(r"-(latest|preview)$", "", k)
    return k.replace(".", "-")


def load_openrouter(refresh: bool = False, timeout: float = 10.0) -> dict[str, dict[str, Any]]:
    """OpenRouter's model list keyed by normalized id; {} if unreachable and nothing is cached."""
    global _table
    with _lock:
        if _table is not None and not refresh:
            return _table
        path = _cache_path()
        data = None
        if not refresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_S:
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                data = None
        if data is None:
            try:
                resp = httpx.get(OPENROUTER_MODELS, timeout=timeout)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
            except (httpx.HTTPError, ValueError, OSError):
                data = json.loads(path.read_text()) if path.exists() else []
        table: dict[str, dict[str, Any]] = {}
        for m in data:
            table.setdefault(_key(m["id"]), m)
            table[m["id"].lower()] = m
        _table = table
        return table


def load_overrides(path: Path | None = None) -> dict[str, Price]:
    path = path or _config_dir() / "prices.toml"
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text())
    out = {}
    for model, v in raw.items():
        if isinstance(v, dict) and "input_per_mtok" in v and "output_per_mtok" in v:
            out[model.lower()] = Price(float(v["input_per_mtok"]) / 1e6, float(v["output_per_mtok"]) / 1e6,
                                       f"{path.name}", model)
    return out


def price_for(backend: str, model: str, *, overrides: dict[str, Price] | None = None,
              table: dict[str, dict[str, Any]] | None = None) -> Price | None:
    if backend == "local":  # the offline mock: a fixed, made-up price so settlement math is testable
        return Price(1e-6, 4e-6, "mock (fixed)", model)
    overrides = load_overrides() if overrides is None else overrides
    for k in (model.lower(), openrouter_id(backend, model).lower()):
        if k in overrides:
            return overrides[k]
    table = load_openrouter() if table is None else table
    oid = openrouter_id(backend, model)
    entry = table.get(oid.lower()) or table.get(_key(oid))
    if not entry:
        return None
    p = entry.get("pricing") or {}
    try:
        inp, out = float(p.get("prompt") or 0), float(p.get("completion") or 0)
    except (TypeError, ValueError):
        return None
    if inp < 0 or out < 0:  # OpenRouter uses -1 for router models with variable prices
        return None
    via = "openrouter" if backend == "openrouter" else f"openrouter list price for {entry['id']}"
    return Price(inp, out, via, entry["id"])


def links_block() -> str:
    return "\n".join(f"  - {name}: {url}" for name, url in PRICE_LINKS.items())


def price_table_md(rows: list[tuple[str, str, Price | None]]) -> str:
    """Markdown table for (label, backend, price) rows."""
    lines = ["| model | backend | input $/1M tok | output $/1M tok | source |", "|---|---|---:|---:|---|"]
    for label, backend, p in rows:
        if p is None:
            lines.append(f"| {label} | {backend} | ? | ? | not found; add it to prices.toml |")
        else:
            i, o = p.per_mtok()
            lines.append(f"| {label} | {backend} | {i:.3f} | {o:.3f} | {p.source} |")
    return "\n".join(lines)
