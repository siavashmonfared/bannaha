"""Research tools: web search, page fetch, document search, Python, and the
finishing tools an independent agent or worker ends its turn with."""
from __future__ import annotations

from typing import Any

from ..core import events as ev
from ..core.text import clip
from ..core.tools import JsonDict, Tool, ToolContext, schema
from .documents import fetch_url
from .sandbox import DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, SandboxBackend
from .web_search import gemini_search


def web_search_tool(model: str) -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        result = gemini_search(query, model=model)
        usage = result.pop("_usage", None) or {}
        stats = ctx.agent.stats
        stats.search_calls += 1
        stats.search_input_tokens += int(usage.get("input") or 0)
        stats.search_output_tokens += int(usage.get("output") or 0)
        return result

    return Tool(
        name="web_search",
        description=("Search the web (Google Search via Gemini). Returns a short grounded summary and the "
                     "source URLs it relied on. Use fetch_page to read a source in full."),
        parameters=schema({"query": {"type": "string", "description": "A focused search query."}}, ["query"]),
        handler=handler,
    )


def fetch_page_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "url must start with http:// or https://"}
        title, text = fetch_url(url)
        doc = ctx.session.documents.add(url, title, text)
        return {"ok": True, "doc_id": doc.id, "title": title, "chars": len(text), "passages": len(doc.passages),
                "excerpt": clip(text, 1500),
                "hint": "Use search_documents to find the passages relevant to your question."}

    return Tool(
        name="fetch_page",
        description="Download a web page or text file, store it for search_documents, and return an excerpt.",
        parameters=schema({"url": {"type": "string", "description": "Absolute http(s) URL."}}, ["url"]),
        handler=handler,
    )


def search_documents_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        doc_id = args.get("doc_id") or None
        hits = ctx.session.documents.search(query, doc_id=doc_id)
        return {"ok": True, "results": hits, "documents": ctx.session.documents.list()}

    return Tool(
        name="search_documents",
        description=("Search the pages fetched so far in this session (by anyone in the fleet) and return the "
                     "most relevant passages with their URLs."),
        parameters=schema({
            "query": {"type": "string", "description": "What you are looking for."},
            "doc_id": {"type": "string", "description": "Optional: restrict to one document id."},
        }, ["query"]),
        handler=handler,
    )


def run_python_tool(backend: SandboxBackend) -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return {"ok": False, "error": "code must be a non-empty string"}
        try:
            timeout = min(float(args.get("timeout_s") or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        return backend.run_python(code, timeout_s=timeout)

    return Tool(
        name="run_python",
        description=(f"Run a Python script ({backend.name} sandbox) and return stdout/stderr. Use print() "
                     "for anything you want to see. Good for arithmetic, unit conversion and parsing."),
        parameters=schema({
            "code": {"type": "string", "description": "Complete Python source to execute."},
            "timeout_s": {"type": "number", "description": f"Wall-time limit, default {DEFAULT_TIMEOUT_S:.0f}s."},
        }, ["code"]),
        handler=handler,
    )


def _sources(args: JsonDict) -> list[str]:
    raw: Any = args.get("sources") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(s) for s in raw][:10]


def final_answer_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        if not str(args.get("answer") or "").strip():
            return {"ok": False, "error": "answer is required"}
        ctx.session.emit(ev.ANSWER_SUBMITTED, agent=ctx.agent.id, answer=clip(args.get("answer"), 200),
                         unit=clip(args.get("unit") or "", 40))
        return {"ok": True, "recorded": True, "sources": _sources(args)}

    return Tool(
        name="final_answer",
        description="Submit your final answer. This ends your turn.",
        parameters=schema({
            "answer": {"type": "string", "description": "The answer, as short as possible."},
            "unit": {"type": "string", "description": "Unit or currency if the answer is a number."},
            "confidence": {"type": "number", "description": "0 to 1."},
            "rationale": {"type": "string", "description": "One or two sentences on why."},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "Source URLs."},
        }, ["answer"]),
        handler=handler,
        terminal=True,
    )

