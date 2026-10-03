"""The harness interface: run one agent turn with a set of tools.

A harness is whose agent loop drives the model (Anthropic SDK, OpenAI
Agents SDK, ...). A backend is how it reaches the model (direct API,
OpenRouter, Bedrock). Each harness declares which backends it supports and
which environment variables each needs, so a fleet can be checked before
anything runs.
"""
from __future__ import annotations

import importlib.util
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from ..core.fleet import Agent
    from ..core.tools import JsonDict, Tool

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

    def report_usage(self, input_tokens: int, output_tokens: int) -> None:
        if self.on_usage is not None:
            self.on_usage(input_tokens, output_tokens)

    @property
    def terminal_names(self) -> list[str]:
        return [t.name for t in self.tools if t.terminal]


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
            problems.append(f"harness {cls.name!r} needs the {cls.requires_module!r} package: "
                            f"uv tool install 'bannaha[{cls.extra}]' (or pip install 'bannaha[{cls.extra}]')")
        for alternatives in cls.requirements(backend, model):
            if not any(os.environ.get(v) for v in alternatives):
                problems.append(f"{cls.name}@{backend} needs {' or '.join(alternatives)} in the environment")
        problems += cls.extra_problems(backend, model)
        return problems

    @abstractmethod
    def run_turn(self, req: TurnRequest) -> HarnessReply:
        ...
