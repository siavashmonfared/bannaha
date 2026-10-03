"""Page 3: API keys. Shows which variables are found and where, never their values."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ..backends import aws_region
from ..cli.config import config_dir, env_files, env_source, load_env_files
from ..core.fleet import parse_fleet
from ..core.registry import harnesses
from ..core.text import tilde
from .banner import Banner
from .widgets import PageBar

if TYPE_CHECKING:
    from .app import BannahaApp

KEYS = [
    (("ANTHROPIC_API_KEY",), "anthropic@direct · banna@direct:anthropic/…"),
    (("OPENAI_API_KEY",), "openai@direct · hermes@direct · banna@direct:openai/…"),
    (("GEMINI_API_KEY", "GOOGLE_API_KEY"), "adk@direct · banna@direct:gemini/… · web search"),
    (("OPENROUTER_API_KEY",), "any harness @openrouter"),
    (("AWS_REGION", "AWS_DEFAULT_REGION"), "any harness @bedrock"),
    (("BANNAHA_HERMES_BASE_URL",), "hermes@local (OpenAI-compatible server)"),
]
TEMPLATE = """# bannaha API keys (VAR=value). Empty values are ignored.
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=
AWS_REGION=
AWS_PROFILE=
"""


def _aws_credentials() -> tuple[bool, str]:
    try:
        import boto3
    except ImportError:
        return False, "boto3 not installed (bannaha[bedrock])"
    try:
        creds = boto3.Session().get_credentials()
    except Exception as exc:  # botocore raises many profile/config errors
        return False, f"error: {exc}"
    if creds is None:
        return False, "none found (AWS_PROFILE, access keys, or `aws sso login`)"
    return True, getattr(creds, "method", "found")


class KeysScreen(Screen):
    BINDINGS: ClassVar = [
        Binding("r", "reload", "reload keys"),
        Binding("e", "edit_file", "edit key file"),
        Binding("q", "app.quit", "quit"),
    ]
    DEFAULT_CSS = """
    KeysScreen DataTable { margin: 1 2 0 2; height: auto; }
    KeysScreen #help { margin: 1 2; height: auto; }
    """

    @property
    def bapp(self) -> BannahaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield PageBar("keys")
        yield Banner()
        yield DataTable(id="keys", cursor_type="row", zebra_stripes=True)
        yield Static(id="help")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("variable", "needed now", "status", "from", "used by")
        self.refresh_table()

    def on_screen_resume(self) -> None:
        self.refresh_table()

    def _needed(self) -> set[str]:
        needed: set[str] = set()
        try:
            entries = parse_fleet(self.bapp.settings.fleet)
        except Exception:  # an invalid fleet just means nothing is "needed" yet
            entries = []
        registry = harnesses()
        for e in entries:
            cls = registry.get(e.harness)
            if cls is None:
                continue
            for alternatives in cls.requirements(e.backend, e.model):
                needed.add(alternatives[0])
            if e.backend == "bedrock":
                needed.add("AWS_REGION")
        if self.bapp.settings.web:
            needed.add("GEMINI_API_KEY")
        return needed

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        needed = self._needed()
        home = os.path.expanduser("~")
        for names, used_by in KEYS:
            found = next((n for n in names if env_source(n)), None)
            label = " or ".join(names)
            need = Text("yes", "bold yellow") if names[0] in needed else Text("–", "dim")
            if found:
                status = Text("✓ found", "green")
                src = env_source(found) or ""
                src = src.replace(home, "~")
            else:
                status = Text("✗ missing", "red" if names[0] in needed else "dim")
                src = ""
            table.add_row(label, need, status, src, used_by)
        ok, how = _aws_credentials()
        need = Text("yes", "bold yellow") if "AWS_REGION" in needed else Text("–", "dim")
        table.add_row("AWS credentials", need, Text("✓ found", "green") if ok else Text("✗ missing", "dim"),
                      how if ok else "", "any harness @bedrock (" + ("ok" if ok else how) + ")")
        files = "\n".join(f"  {tilde(p)}" + ("" if p.is_file() else "  (not created)") for p in env_files())
        help_ = Text()
        help_.append("Paste keys as VAR=value into:\n", "bold")
        help_.append(f"  {tilde(config_dir() / '.env')}   ← recommended: read from any directory, never committed\n")
        help_.append("Also read: a .env in the directory you start bannaha from, and your shell environment "
                     "(which wins).\n\n", "dim")
        help_.append(f"Files checked:\n{files}\n\n", "dim")
        help_.append(f"AWS region: {aws_region() or 'not set'}\n\n", "dim")
        help_.append("e edit the key file in $EDITOR · r reload after editing it elsewhere · 1–5 pages", "dim")
        self.query_one("#help", Static).update(help_)

    def action_reload(self) -> None:
        load_env_files(reload=True)
        self.refresh_table()
        self.notify("keys reloaded")

    def action_edit_file(self) -> None:
        path = config_dir() / ".env"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(TEMPLATE)
        os.chmod(path, 0o600)
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or \
            next((e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
        if editor is None:
            self.notify(f"no editor found; edit {path} yourself, then press r", severity="warning")
            return
        with self.app.suspend():
            subprocess.run([*editor.split(), str(path)], check=False)
        self.action_reload()
