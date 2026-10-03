"""Writes a run to disk: events.jsonl as it happens, result.json at the end."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from . import events as ev
from .events import Event


def default_runs_dir() -> Path:
    base = os.environ.get("BANNAHA_RUNS_DIR")
    if base:
        return Path(base).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(xdg).expanduser() / "bannaha" / "runs"


def new_run_dir(strategy: str, root: Path | None = None) -> Path:
    root = root or default_runs_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = root / f"{stamp}-{re.sub(r'[^a-z0-9_-]', '', strategy.lower())}"
    i = 1
    while path.exists():
        i += 1
        path = root / f"{stamp}-{strategy}-{i}"
    path.mkdir(parents=True)
    return path


class RunLog:
    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir
        self._lock = threading.Lock()
        self._fh = (run_dir / "events.jsonl").open("a", encoding="utf-8")
        self._started: dict | None = None

    def __call__(self, event: Event) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            if event.kind == ev.SESSION_STARTED:
                self._started = event.data
            elif event.kind == ev.SESSION_FINISHED:
                result = {**(self._started or {}), **event.data}
                report = ((result.get("outcome") or {}).get("detail") or {}).get("report_md")
                if report:
                    (self.dir / "report.md").write_text(report + "\n", encoding="utf-8")
                (self.dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
                self._fh.close()
