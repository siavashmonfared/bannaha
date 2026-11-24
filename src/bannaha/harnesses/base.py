"""The harness interface: run one agent turn with a set of tools.

A harness is whose agent loop drives the model (Anthropic SDK, OpenAI
Agents SDK, ...). A backend is how it reaches the model (direct API,
OpenRouter, Bedrock). Each harness declares which backends it supports and
which environment variables each needs, so a fleet can be checked before
anything runs.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from ..core.fleet import Agent
    from ..core.tools import JsonDict, Tool

REINSTALL = "reinstall bannaha: uv tool install --force bannaha (or pip install --force-reinstall bannaha)"

NUDGE = ("You ended your turn without calling a finishing tool. Call one of these now: {names}. "
         "If you are unsure, give your best answer and say so in the rationale.")


@dataclass
class TurnRequest:
    agent: Agent
    system: str
    prompt: str
    tools: list[Tool]
    call_tool: Callable[[Tool, Any], JsonDict]
    is_done: Callable[[], bool]
    max_steps: int
    max_output_tokens: int
    # Harnesses that see usage per model call report the turn's running totals here, so cost limits and
    # the live view work mid-turn. Others report only at the end, through HarnessReply.
    on_usage: Callable[[int, int], None] | None = None
    # When True, a harness that sees the model's text may recover a tool call written there (coerce_tool_call).
    lenient: bool = True

    def report_usage(self, input_tokens: int, output_tokens: int) -> None:
        if self.on_usage is not None:
            self.on_usage(input_tokens, output_tokens)

    @property
    def terminal_names(self) -> list[str]:
        return [t.name for t in self.tools if t.terminal]


# ---------------------------------------------------------------------------
# Lenient parsing: tool calls that small models write as text
# ---------------------------------------------------------------------------

_ARG_KEYS = ("arguments", "parameters", "args", "input")
_KV_LINE = re.compile(r"""^\s*[-*]?\s*["']?([A-Za-z_][\w-]*)["']?\s*[:=]\s*(.+?)\s*,?\s*$""")


def _json_objects(text: str) -> list[Any]:
    """Every JSON object embedded in `text` (code fences and surrounding prose are ignored)."""
    dec, out, i = json.JSONDecoder(), [], 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        out.append(obj)
        i = end
    return out


def _complete(tool: Tool, args: Any) -> dict[str, Any] | None:
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    props = (tool.parameters or {}).get("properties") or {}
    if props:
        args = {k: v for k, v in args.items() if k in props}
    required = (tool.parameters or {}).get("required") or []
    return args if all(k in args for k in required) else None


def _value(raw: str) -> Any:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except ValueError:
        return raw.strip("\"'")


def _python_call(text: str, tool: Tool) -> dict[str, Any] | None:
    """`name(a, b, key=c)`: positional arguments follow the schema's property order."""
    m = re.search(rf"\b{re.escape(tool.name)}\s*\(", text)
    if not m:
        return None
    depth, start = 0, m.end() - 1
    for j in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            src = "f" + text[start:j + 1]
            break
    else:
        return None
    try:
        call = ast.parse(src, mode="eval").body
        if not isinstance(call, ast.Call):
            return None
        names = list(((tool.parameters or {}).get("properties") or {}).keys())
        args = {names[i]: ast.literal_eval(a) for i, a in enumerate(call.args) if i < len(names)}
        args.update({k.arg: ast.literal_eval(k.value) for k in call.keywords if k.arg})
    except (SyntaxError, ValueError, TypeError):
        return None
    return args


def coerce_tool_call(text: str, tools: list[Tool]) -> tuple[Tool, dict[str, Any]] | None:
    """Recover a tool call the model wrote in its text instead of calling the tool.

    Recognized, in order:
      1. a JSON object {"name": <tool>, "arguments": {...}} (also "parameters"/"args"/"input"; the
         arguments may themselves be a JSON string);
      2. `<tool>(a, b, key=c)`, positional arguments mapped onto the schema's properties in order;
      3. with exactly one tool offered: a bare JSON object of its arguments, or `key: value` lines.
    A recovered call must name an offered tool and carry every required argument; otherwise None.
    """
    if not text or not tools:
        return None
    by_name = {t.name: t for t in tools}
    objects = _json_objects(text)
    for obj in objects:
        if isinstance(obj, dict) and obj.get("name") in by_name:
            tool = by_name[obj["name"]]
            raw = next((obj[k] for k in _ARG_KEYS if k in obj), {})
            args = _complete(tool, raw)
            if args is not None:
                return tool, args
    for tool in tools:
        found = _python_call(text, tool)
        if found is not None and (args := _complete(tool, found)) is not None:
            return tool, args
    if len(tools) == 1:
        tool = tools[0]
        for obj in objects:
            if isinstance(obj, dict) and obj and (args := _complete(tool, obj)):
                return tool, args
        props = (tool.parameters or {}).get("properties") or {}
        pairs = {m.group(1): _value(m.group(2)) for line in text.splitlines() if (m := _KV_LINE.match(line))
                 and m.group(1) in props}
        if pairs and (args := _complete(tool, pairs)):
            return tool, args
    return None


@dataclass
class HarnessReply:
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    steps: int = 0


class Harness(ABC):
    name: ClassVar[str]
    summary: ClassVar[str] = ""
    # backend name -> required env vars; each entry is a tuple of alternatives.
    backends: ClassVar[dict[str, list[tuple[str, ...]]]] = {}
    # python module that must be importable, and the extra that installs it.
    requires_module: ClassVar[str | None] = None
    extra: ClassVar[str | None] = None
    model_hints: ClassVar[dict[str, list[str]]] = {}

    @classmethod
    def available(cls) -> bool:
        return cls.requires_module is None or importlib.util.find_spec(cls.requires_module) is not None

    @classmethod
    def requirements(cls, backend: str, model: str) -> list[tuple[str, ...]]:
        """Env vars this backend/model needs; override when they depend on the model."""
        return cls.backends.get(backend, [])

    @classmethod
    def extra_problems(cls, backend: str, model: str) -> list[str]:
        """Anything else to verify before a run (credentials, optional packages)."""
        return []

    @classmethod
    def check(cls, backend: str, model: str = "") -> list[str]:
        problems: list[str] = []
        if backend not in cls.backends:
            supported = ", ".join(sorted(cls.backends)) or "none"
            problems.append(f"harness {cls.name!r} does not support backend {backend!r} (supported: {supported})")
            return problems
        if not cls.available():
            problems.append(f"harness {cls.name!r} needs the {cls.requires_module!r} package, which ships with "
                            f"bannaha but is missing from this install; {REINSTALL}")
        for alternatives in cls.requirements(backend, model):
            if not any(os.environ.get(v) for v in alternatives):
                problems.append(f"{cls.name}@{backend} needs {' or '.join(alternatives)} in the environment")
        problems += cls.extra_problems(backend, model)
        return problems

    @abstractmethod
    def run_turn(self, req: TurnRequest) -> HarnessReply:
        ...
