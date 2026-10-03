"""Fetched pages, split into passages and searchable with a small BM25."""
from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

from ..core.text import clip

MAX_BYTES = 3_000_000
_WORD = re.compile(r"[a-z0-9][a-z0-9.%$-]*")


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return p.title.strip(), text


def fetch_url(url: str, timeout: float = 15.0) -> tuple[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; bannaha/0.1; +https://pypi.org/project/bannaha/)"}
    with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        chunks, size = [], 0
        for chunk in r.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                break
        body = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
        ctype = r.headers.get("content-type", "")
    if "html" in ctype or body.lstrip().startswith("<"):
        return html_to_text(body)
    return "", body


def _tokens(s: str) -> list[str]:
    return _WORD.findall(s.lower())


@dataclass
class Document:
    id: str
    url: str
    title: str
    passages: list[str] = field(default_factory=list)


class DocumentStore:
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self._by_url: dict[str, str] = {}
        self._lock = threading.Lock()

    def add(self, url: str, title: str, text: str) -> Document:
        with self._lock:
            if url in self._by_url:
                return self._docs[self._by_url[url]]
            paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 30]
            passages: list[str] = []
            buf = ""
            for para in paragraphs:
                if len(buf) + len(para) > 900 and buf:
                    passages.append(buf)
                    buf = ""
                buf = f"{buf}\n{para}".strip()
            if buf:
                passages.append(buf)
            doc = Document(id=f"d{len(self._docs) + 1}", url=url, title=title, passages=passages)
            self._docs[doc.id] = doc
            self._by_url[url] = doc.id
            return doc

    def list(self) -> list[dict[str, str]]:
        with self._lock:
            return [{"doc_id": d.id, "url": d.url, "title": d.title} for d in self._docs.values()]

    def search(self, query: str, *, doc_id: str | None = None, k: int = 5) -> list[dict[str, str]]:
        with self._lock:
            docs = [self._docs[doc_id]] if doc_id in self._docs else list(self._docs.values())
        corpus = [(d, i, p, _tokens(p)) for d in docs for i, p in enumerate(d.passages)]
        if not corpus:
            return []
        q = set(_tokens(query))
        n = len(corpus)
        avg = sum(len(t) for *_, t in corpus) / n
        df = Counter(w for *_, t in corpus for w in set(t))
        scored = []
        for d, i, p, toks in corpus:
            tf = Counter(toks)
            score = 0.0
            for w in q:
                if tf[w]:
                    idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                    score += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(toks) / avg))
            if score > 0:
                scored.append((score, d, i, p))
        scored.sort(key=lambda x: -x[0])
        return [{"doc_id": d.id, "url": d.url, "passage": i, "text": clip(p, 900)} for _, d, i, p in scored[:k]]
