"""The per-session tool set. Strategies ask for tools by name; tools that are
switched off (for example web tools under --no-web) are simply left out."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..core.tools import Tool
from . import comms, research
from .sandbox import resolve_backend
from .web_search import DEFAULT_SEARCH_MODEL

if TYPE_CHECKING:
    from ..core.session import Session

RESEARCH = ("web_search", "fetch_page", "search_documents", "run_python")
COMMS = ("read_board", "post_claim", "endorse_claim", "challenge_claim", "corroborate", "send_message")
WEB_TOOLS = ("web_search", "fetch_page", "search_documents")


@dataclass
class ToolOptions:
    web: bool = True
    python: bool = True
    sandbox: str = "process"
    search_model: str = DEFAULT_SEARCH_MODEL

    def describe(self) -> dict[str, Any]:
        return {"web": self.web, "python": self.python, "sandbox": self.sandbox if self.python else None,
                "search_model": self.search_model if self.web else None}


class Toolbox:
    def __init__(self, session: Session, options: ToolOptions) -> None:
        self.session = session
        self.options = options
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        if options.web:
            for t in (research.web_search_tool(options.search_model), research.fetch_page_tool(),
                      research.search_documents_tool()):
                self.add(t)
        else:
            self._disabled.update(WEB_TOOLS)
        if options.python:
            self.add(research.run_python_tool(resolve_backend(options.sandbox)))
        else:
            self._disabled.add("run_python")
        for t in (research.final_answer_tool(), comms.read_board_tool(),
                  comms.post_claim_tool(), comms.endorse_claim_tool(), comms.challenge_claim_tool(),
                  comms.corroborate_tool(), comms.send_message_tool(), comms.read_messages_tool(),
                  comms.propose_answer_tool(),
                  comms.cast_vote_tool()):
            self.add(t)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, names: Iterable[str]) -> list[Tool]:
        out: list[Tool] = []
        for name in names:
            if name in self._disabled:
                continue
            if name not in self._tools:
                raise KeyError(f"unknown tool {name!r}; known: {', '.join(sorted(self._tools))}")
            out.append(self._tools[name])
        return out

    def names(self) -> list[str]:
        return sorted(self._tools)
