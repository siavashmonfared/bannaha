import os
import tempfile

# Before any bannaha or Hermes import: keep Hermes out of the real ~/.hermes and ~/.cache.
os.environ["BANNAHA_HERMES_HOME"] = tempfile.mkdtemp(prefix="bannaha-test-hermes-")

import pytest  # noqa: E402

from bannaha.core.fleet import parse_fleet  # noqa: E402
from bannaha.core.registry import strategies  # noqa: E402
from bannaha.core.session import Session  # noqa: E402
from bannaha.tools.toolbox import ToolOptions  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("BANNAHA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("BANNAHA_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("BANNAHA_MOCK_DELAY", "0")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def make_session():
    def _make(strategy: str, fleet: str, features=(), question="What is the answer?", web=False, python=False,
              **opts):
        s = Session(question=question, fleet=parse_fleet(fleet), strategy=strategies()[strategy](**opts),
                    tool_options=ToolOptions(web=web, python=python), features=list(features))
        events = []
        s.bus.subscribe(events.append)
        s.events = events
        return s
    return _make
