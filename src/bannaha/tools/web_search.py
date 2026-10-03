"""Web search through Gemini with Google Search grounding.

Search runs as its own Gemini call inside the tool, so agents on any
harness or model can use it. Needs GEMINI_API_KEY (or GOOGLE_API_KEY).
"""
from __future__ import annotations

import os
import threading
from typing import Any

import httpx

from ..core.text import clip

DEFAULT_SEARCH_MODEL = "gemini-2.5-flash"
_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_redirects: dict[str, str] = {}
_redirect_lock = threading.Lock()
_client = None
_client_lock = threading.Lock()


def search_key_present() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from google import genai

            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            _client = genai.Client(api_key=key)
        return _client


def resolve_redirect(url: str, timeout: float = 3.0) -> str:
    """Grounding citations come back as Google redirect links; follow them to
    the publisher URL so sources are readable and checkable. Fails open."""
    if _REDIRECT_HOST not in url:
        return url
    with _redirect_lock:
        if url in _redirects:
            return _redirects[url]
    try:
        final = str(httpx.head(url, follow_redirects=True, timeout=timeout).url)
    except httpx.HTTPError:
        final = url
    with _redirect_lock:
        _redirects[url] = final
    return final


def gemini_search(query: str, *, model: str = DEFAULT_SEARCH_MODEL) -> dict[str, Any]:
    from google.genai import types

    resp = _get_client().models.generate_content(
        model=model,
        contents=("Search the web and answer factually and concisely. Include exact figures, dates "
                  f"and names where relevant.\n\nQuery: {query}"),
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    sources: list[dict[str, str]] = []
    queries: list[str] = []
    candidates = getattr(resp, "candidates", None) or []
    meta = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    if meta is not None:
        queries = list(getattr(meta, "web_search_queries", None) or [])
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web is not None and getattr(web, "uri", None):
                sources.append({"title": web.title or "", "url": resolve_redirect(web.uri)})
    seen: set[str] = set()
    unique = [s for s in sources if not (s["url"] in seen or seen.add(s["url"]))]
    usage = getattr(resp, "usage_metadata", None)
    tokens = {"input": (getattr(usage, "prompt_token_count", 0) or 0)
              + (getattr(usage, "tool_use_prompt_token_count", 0) or 0),
              "output": (getattr(usage, "candidates_token_count", 0) or 0)
              + (getattr(usage, "thoughts_token_count", 0) or 0)}
    return {"ok": True, "summary": clip(resp.text or "", 4000), "sources": unique[:8], "queries": queries,
            "_usage": tokens}
