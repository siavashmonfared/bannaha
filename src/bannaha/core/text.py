import json
import re
from typing import Any

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ROLE_PREFIX = re.compile(r"^\s*(system|assistant|user|developer)\s*:\s*", re.IGNORECASE | re.MULTILINE)


def clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def clean_peer_text(text: Any, limit: int = 600) -> str:
    """Sanitize text written by one agent before another agent reads it."""
    s = _CONTROL.sub("", "" if text is None else str(text))
    s = _ROLE_PREFIX.sub("", s)
    return clip(s.strip(), limit)


def summarize_args(args: dict[str, Any], limit: int = 160) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(args)
    return clip(s, limit)


def tilde(path: Any) -> str:
    """A path with the home directory shown as ~."""
    import os

    s, home = str(path), os.path.expanduser("~")
    return "~" + s[len(home):] if home and s.startswith(home) else s


def to_json(obj: Any, limit: int | None = None) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if limit is None else clip(s, limit)


def result_preview(result: Any, limit: int = 4000) -> str:
    """The readable part of a tool result, for transcripts: the text a person would want to see."""
    if not isinstance(result, dict):
        return clip(result, limit)
    if not result.get("ok", True) and result.get("error"):
        return clip(f"error: {result['error']}", limit)
    parts: list[str] = []
    for key in ("summary", "title", "excerpt", "content", "stdout", "log", "path"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    if result.get("stderr") and str(result["stderr"]).strip():
        parts.append("stderr: " + str(result["stderr"]).strip())
    for key in ("sources", "results", "items", "entries", "jobs", "reports", "matches"):
        val = result.get(key)
        if isinstance(val, list) and val:
            lines = []
            for item in val[:8]:
                if isinstance(item, dict):
                    head = item.get("url") or item.get("path") or item.get("title") or item.get("id") or ""
                    body = item.get("text") or item.get("summary") or item.get("status") or item.get("name") or ""
                    lines.append(f"- {head} {clip(body, 200)}".rstrip())
                else:
                    lines.append(f"- {clip(item, 200)}")
            if len(val) > 8:
                lines.append(f"… {len(val) - 8} more")
            parts.append("\n".join(lines))
    if isinstance(result.get("job"), dict):
        j = result["job"]
        parts.append(f"job {j.get('id')} [{j.get('status')}] {j.get('name', '')}".strip())
    if not parts:
        rest = {k: v for k, v in result.items() if k not in ("ok", "note", "hint", "budget_note")}
        if not rest:
            return "ok"
        try:
            parts.append(json.dumps(rest, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            parts.append(str(rest))
    return clip("\n".join(parts), limit)
