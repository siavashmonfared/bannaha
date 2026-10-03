"""Isolation backends for `run_python`.

* `process` (default): a host subprocess with a wall-time kill. It shares
  your filesystem, network and credentials. The threat model is "the model
  writes runaway or wrong code", not "hostile code".
* `docker`: a throwaway container with no network, a read-only root
  filesystem, dropped capabilities, a non-root user and cpu/memory/pid caps.

Pick with `--sandbox process|docker` or BANNAHA_SANDBOX.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 120.0
MAX_OUTPUT_CHARS = 20_000


def _shape(returncode: int, stdout: Any, stderr: Any, timed_out: bool, wall_s: float) -> dict[str, Any]:
    stdout = (stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout) or ""
    stderr = (stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr) or ""
    ok = returncode == 0 and not timed_out
    result: dict[str, Any] = {
        "ok": ok, "returncode": returncode,
        "stdout": stdout[:MAX_OUTPUT_CHARS], "stderr": stderr[:MAX_OUTPUT_CHARS],
        "timeout": timed_out, "truncated": len(stdout) > MAX_OUTPUT_CHARS or len(stderr) > MAX_OUTPUT_CHARS,
        "wall_s": round(wall_s, 3),
    }
    if timed_out:
        result["error"] = "timed out"
    elif not ok:
        last = stderr.strip().splitlines()[-1] if stderr.strip() else "(no stderr)"
        result["error"] = f"python exited with code {returncode}: {last}"
    return result


class SandboxBackend(ABC):
    name = "base"

    @abstractmethod
    def run_python(self, code: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        ...


class ProcessBackend(SandboxBackend):
    name = "process"

    def run_python(self, code: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="bannaha-py-") as work:
            script = Path(work) / "main.py"
            script.write_text(code)
            t0 = time.monotonic()
            try:
                p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                   timeout=timeout_s, cwd=work)
                return _shape(p.returncode, p.stdout, p.stderr, False, time.monotonic() - t0)
            except subprocess.TimeoutExpired as e:
                return _shape(-1, e.stdout, e.stderr, True, time.monotonic() - t0)


class DockerBackend(SandboxBackend):
    name = "docker"

    def __init__(self, image: str = "python:3.12-slim", memory: str = "512m", cpus: str = "1.0",
                 pids: int = 256, docker_bin: str = "docker") -> None:
        self.image, self.memory, self.cpus, self.pids, self.docker_bin = image, memory, cpus, pids, docker_bin

    def run_python(self, code: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        argv = [
            self.docker_bin, "run", "--rm", "-i",
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec,size=256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", self.memory, "--cpus", self.cpus, "--pids-limit", str(self.pids),
            "--user", "1000:1000", "-w", "/tmp", self.image, "python", "-",
        ]
        t0 = time.monotonic()
        try:
            p = subprocess.run(argv, input=code, capture_output=True, text=True, timeout=timeout_s)
            return _shape(p.returncode, p.stdout, p.stderr, False, time.monotonic() - t0)
        except subprocess.TimeoutExpired as e:
            return _shape(-1, e.stdout, e.stderr, True, time.monotonic() - t0)
        except FileNotFoundError:
            return _shape(-2, "", f"{self.docker_bin!r} not found on PATH; install Docker or use "
                                  "--sandbox process", False, time.monotonic() - t0)


def resolve_backend(mode: str | None = None) -> SandboxBackend:
    name = (mode or os.environ.get("BANNAHA_SANDBOX") or "process").strip().lower()
    if name in ("process", "host"):
        return ProcessBackend()
    if name == "docker":
        image = os.environ.get("BANNAHA_SANDBOX_IMAGE")
        return DockerBackend(image=image) if image else DockerBackend()
    raise ValueError(f"unknown sandbox {name!r} (expected 'process' or 'docker')")
