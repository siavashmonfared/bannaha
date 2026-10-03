"""bannaha command line: `bannaha` opens the full-screen interface, `bannaha run` is the scriptable path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table

from .. import __version__
from ..core.errors import BannahaError
from ..core.fleet import parse_fleet
from ..core.outcome import Outcome
from ..core.pricing import PRICE_LINKS, load_openrouter, price_for, price_table_md
from ..core.registry import harnesses, strategies
from ..core.runlog import default_runs_dir
from ..core.text import clip, tilde
from ..tools.web_search import search_key_present
from .config import Settings, config_path, load_env_files, load_settings
from .live import run_live, run_plain
from .runner import RunArgs, prepare
from .themes import THEMES, get_theme
from .transcript import fleet_colors, run_stream

app = typer.Typer(
    name="bannaha",
    help="A terminal sandbox for multi-agent coordination strategies. Run with no arguments for the full-screen app.",
    add_completion=False,
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def execute(question: str, a: RunArgs, s: Settings, console: Console) -> Outcome | None:
    theme = get_theme(a.theme or s.theme)
    try:
        prepared, problems = prepare(question, s, a)
    except BannahaError as exc:
        prepared, problems = None, [str(exc)]
    if prepared is None:
        console.print(f"[{theme.err}]cannot start:[/]")
        for p in problems:
            console.print(f"  • {escape(p)}")
        return None
    captured: list = []
    prepared.session.bus.subscribe(captured.append)
    if a.plain or not console.is_terminal:
        outcome = run_plain(prepared.session, console, theme, prepared.run_dir)
    elif a.dashboard:
        outcome = run_live(prepared.session, console, theme, prepared.run_dir)
    else:
        outcome = run_stream(prepared.session, console, theme, prepared.run_dir, full=a.full)
    if a.json_out and outcome is not None:
        run_dir = str(prepared.run_dir) if prepared.run_dir else None
        console.print_json(json.dumps({"run_dir": run_dir, **outcome.to_dict()}, default=str))
    elif a.followup and outcome is not None and console.is_terminal and sys.stdin.isatty() and not a.plain:
        from ..followup import RunMemory

        followup_loop(console, theme, s, RunMemory.from_events(captured, prepared.run_dir), a)
    return outcome


def followup_loop(console: Console, theme, s: Settings, memory, a: RunArgs | None = None, *,
                  first: str | None = None, once: bool = False, agent: str | None = None) -> None:
    """Questions about a finished run, with the run as memory; /new and /rerun start runs."""
    from ..followup import FollowUp, RunMemory, help_renderable, parse_command, rerun_question
    from ..tools.toolbox import ToolOptions
    from .runner import limits_for

    chat = FollowUp(memory, agent=agent)
    opts = ToolOptions(web=s.web if (a is None or a.web is None) else a.web,
                       python=s.python if (a is None or a.python is None) else a.python,
                       sandbox=(a.sandbox if a and a.sandbox else s.sandbox), search_model=s.search_model)
    if not once:
        console.print(f"\n[{theme.muted}]Ask about the answer: follow-ups go to[/] [bold]{chat.agent_id()} "
                      f"{escape(chat.agent)}[/] [{theme.muted}](the strongest model in the fleet). /help for "
                      f"commands, ctrl-d to exit.[/]")
    pending = first
    while True:
        if pending is None:
            try:
                text = console.input(f"[bold {theme.accent}]› [/]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return
        else:
            text, pending = pending, None
        if not text:
            continue
        cmd, arg = parse_command(text)
        if cmd in ("/exit", "/quit"):
            return
        if cmd == "/help":
            console.print(help_renderable(theme))
        elif cmd in ("/new", "/rerun"):
            if not arg:
                console.print(f"[{theme.warn}]usage: {cmd} <{'task' if cmd == '/new' else 'clarification'}>[/]")
                continue
            question = arg if cmd == "/new" else rerun_question(chat.memory, arg)
            prepared, problems = prepare(question, s, RunArgs(followup=False))
            if prepared is None:
                for p in problems:
                    console.print(f"  • {escape(p)}")
                continue
            captured: list = []
            prepared.session.bus.subscribe(captured.append)
            run_stream(prepared.session, console, theme, prepared.run_dir)
            chat = FollowUp(RunMemory.from_events(captured, prepared.run_dir))
            console.print(f"[{theme.muted}]follow-ups now go to[/] [bold]{chat.agent_id()} {escape(chat.agent)}[/]")
        elif cmd == "/to":
            try:
                label = chat.set_agent(arg)
                console.print(f"[{theme.muted}]follow-ups now go to[/] [bold]{chat.agent_id()} {escape(label)}[/]")
            except BannahaError as exc:
                console.print(f"[{theme.err}]{escape(str(exc))}[/]")
        elif cmd == "/agents":
            for x in chat.memory.fleet:
                mark = "  ← answers follow-ups" if x["label"] == chat.agent else ""
                console.print(f"  {x['id']}  {escape(x['label'])}{mark}")
        elif cmd == "/forget":
            chat.forget()
            console.print(f"[{theme.muted}]follow-up conversation cleared (the run's memory stays)[/]")
        elif cmd is not None:
            console.print(f"[{theme.warn}]unknown command {escape(cmd)}; /help lists them[/]")
        else:
            session = chat.session(text, opts, limits_for(s, a))
            outcome = run_stream(session, console, theme, None, followup=True, colors=fleet_colors(chat.memory.fleet))
            chat.record(text, outcome, session.total_cost())
        if once:
            return


def _open_tui(start: str) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        Console(stderr=True).print("bannaha's full-screen app needs a terminal; use `bannaha run` in scripts "
                                   "(see `bannaha --help`).")
        raise typer.Exit(2)
    from ..tui.app import run_tui

    run_tui(start)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context,
         version: bool = typer.Option(False, "--version", "-V", is_eager=True, help="Show version and exit.")) -> None:
    if version:
        typer.echo(f"bannaha {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _open_tui("setup")


@app.command()
def run(
    question: str = typer.Argument(None, help="The question. Omit to be prompted."),
    strategy: str = typer.Option(None, "--strategy", "-s", help="Coordination strategy (see `bannaha list`)."),
    fleet: str = typer.Option(None, "--fleet", "-f", help="harness@backend:model*count, comma separated."),
    web: bool = typer.Option(None, "--web/--no-web", help="Gemini web search and page fetch."),
    python: bool = typer.Option(None, "--python/--no-python", help="The run_python tool."),
    sandbox: str = typer.Option(None, "--sandbox", help="run_python isolation: process or docker."),
    peer_updates: bool = typer.Option(None, "--peer-updates/--no-peer-updates",
                                      help="Attach peers' new board activity to tool results."),
    drop: float = typer.Option(None, "--drop", help="Fault injection: fraction (<1) or number of agents to drop."),
    drop_after: int = typer.Option(None, "--drop-after", help="Dropped agents go dark after this many tool calls."),
    seed: int = typer.Option(None, "--seed", help="Seed for choosing which agents drop."),
    opt: list[str] = typer.Option([], "--opt", "-o", help="Strategy option key=value (repeatable)."),
    max_wall: float = typer.Option(None, "--max-wall", help="Wall-time limit in seconds."),
    max_calls: int = typer.Option(None, "--max-calls", help="Tool calls allowed per agent turn."),
    max_tokens: int = typer.Option(None, "--max-tokens", help="Stop after this many tokens in total."),
    max_cost: float = typer.Option(None, "--max-cost", help="Stop the run at this spend in USD (list prices)."),
    max_agent_cost: float = typer.Option(None, "--max-agent-cost",
                                         help="End an agent's work once it has spent this much in USD."),
    search_model: str = typer.Option(None, "--search-model", help="Gemini model used for web search."),
    theme: str = typer.Option(None, "--theme", help=f"Color theme: {', '.join(THEMES)}."),
    plain: bool = typer.Option(False, "--plain", help="Print one line per event instead of the live view."),
    dashboard: bool = typer.Option(False, "--dashboard", help="The boxed dashboard view instead of the transcript."),
    full: bool = typer.Option(False, "--full", help="Show tool results in full in the transcript."),
    json_out: bool = typer.Option(False, "--json", help="Print the outcome as JSON at the end."),
    runs_dir: Path = typer.Option(None, "--runs-dir", help=f"Where runs are saved (default {default_runs_dir()})."),
    no_save: bool = typer.Option(False, "--no-save", help="Do not write the run to disk."),
    no_followup: bool = typer.Option(False, "--no-followup", help="Exit when the run ends instead of taking "
                                                                  "follow-up questions."),
) -> None:
    """Run one question without the full-screen app (scripts, CI, quick checks)."""
    settings, _ = load_settings()
    console = Console()
    if not question:
        question = Prompt.ask("question")
    args = RunArgs(strategy=strategy, fleet=fleet, web=web, python=python, sandbox=sandbox,
                   peer_updates=peer_updates, drop=drop, drop_after=drop_after, seed=seed, opts=list(opt),
                   max_wall=max_wall, max_calls=max_calls, max_tokens=max_tokens, max_cost=max_cost,
                   max_agent_cost=max_agent_cost, search_model=search_model,
                   theme=theme, plain=plain, json_out=json_out, runs_dir=runs_dir, save=not no_save,
                   dashboard=dashboard, full=full, followup=not no_followup)
    outcome = execute(question, args, settings, console)
    if outcome is None:
        raise typer.Exit(2)
    raise typer.Exit(0 if outcome.status == "committed" else 1)


@app.command()
def ask(
    run_dir: str = typer.Argument("last", help="A run folder, or 'last' for the most recent run."),
    question: str = typer.Argument(None, help="One question; omit it to keep asking."),
    to: str = typer.Option(None, "--to", help="Who answers: a fleet id (a3) or harness@backend:model."),
) -> None:
    """Ask follow-up questions about a finished run, with that run as memory."""
    from ..followup import RunMemory

    settings, _ = load_settings()
    console = Console()
    theme = get_theme(settings.theme)
    if run_dir == "last":
        root = default_runs_dir()
        runs = sorted((p for p in root.iterdir() if (p / "events.jsonl").is_file()), reverse=True) \
            if root.is_dir() else []
        if not runs:
            console.print("no saved runs yet")
            raise typer.Exit(2)
        path = runs[0]
    else:
        path = Path(run_dir).expanduser()
    memory = RunMemory.from_dir(path)
    console.print(f"[{theme.muted}]run[/] {tilde(path)} · {escape(clip(memory.question, 120))} → "
                  f"[bold]{escape(str((memory.outcome or {}).get('answer')))}[/]")
    agent = None
    if to:
        by_id = {a["id"]: a["label"] for a in memory.fleet}
        agent = by_id.get(to, to)
    followup_loop(console, theme, settings, memory, first=question, once=question is not None, agent=agent)


@app.command()
def setup() -> None:
    """Open the full-screen app on the Setup page."""
    _open_tui("setup")


@app.command("list")
def list_cmd(what: str = typer.Argument("all", help="strategies, harnesses, tools or all")) -> None:
    """Show what is available."""
    console = Console()
    t = get_theme(load_settings()[0].theme)
    if what in ("all", "strategies"):
        table = Table(title="strategies", title_justify="left", box=None)
        table.add_column("name", style=t.accent)
        table.add_column("summary")
        table.add_column("options", style=t.muted)
        for name, cls in strategies().items():
            opts = ", ".join(f"{k}={o.default}" for k, o in cls.options.items())
            table.add_row(name, cls.summary, opts)
        console.print(table)
    if what in ("all", "harnesses"):
        table = Table(title="harnesses", title_justify="left", box=None)
        table.add_column("name", style=t.accent)
        table.add_column("backends")
        table.add_column("installed")
        table.add_column("summary", style=t.muted)
        for name, cls in harnesses().items():
            installed = "yes" if cls.available() else escape(f"no: bannaha[{cls.extra}]")
            table.add_row(name, ", ".join(cls.backends), installed, cls.summary)
        console.print(table)
    if what in ("all", "tools"):
        console.print(f"[{t.title}]tools[/]")
        console.print("  research  web_search, fetch_page, search_documents (need --web), run_python")
        console.print("  board     read_board, post_claim, endorse_claim, challenge_claim, corroborate, send_message, "
                      "read_messages")
        console.print("  finish    final_answer, propose_answer, cast_vote, reply (follow-ups)")
        console.print(f"  web search key: {'found' if search_key_present() else 'missing (GEMINI_API_KEY)'}")


@app.command("prices")
def prices_cmd(
    fleet: str = typer.Option(None, "--fleet", "-f", help="Fleet to price (default: your saved fleet)."),
    find: str = typer.Option(None, "--find", help="List OpenRouter models whose id contains this text."),
    md: Path = typer.Option(None, "--md", help="Also write the table, with the pricing links, to this .md file."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-download OpenRouter's price list."),
) -> None:
    """Per-token list prices used to settle runs (OpenRouter's public list, or your prices.toml)."""
    s = load_settings()[0]
    t = get_theme(s.theme)
    console = Console()
    table = load_openrouter(refresh=refresh)
    if not table:
        console.print(f"[{t.warn}]could not reach OpenRouter and nothing is cached; only prices.toml applies[/]")
    if find:
        ids = sorted({m["id"] for m in table.values() if find.lower() in m["id"].lower()})
        rows = [(i, "openrouter", price_for("openrouter", i, table=table)) for i in ids]
    else:
        rows, seen = [], set()
        for e in parse_fleet(fleet or s.fleet):
            if e.label not in seen:
                seen.add(e.label)
                rows.append((e.label, e.backend, price_for(e.backend, e.model, table=table)))
        if s.web:
            rows.append((f"web search: {s.search_model}", "direct", price_for("direct", s.search_model, table=table)))
    out = Table(box=None, title="list prices, USD per 1M tokens", title_justify="left")
    for col in ("model", "backend", "input", "output", "source"):
        out.add_column(col, style=t.accent if col == "model" else None,
                       justify="right" if col in ("input", "output") else "left")
    for label, backend, p in rows:
        if p is None:
            out.add_row(escape(label), backend, "?", "?", f"[{t.warn}]not found; add it to prices.toml[/]")
        else:
            i, o = p.per_mtok()
            out.add_row(escape(label), backend, f"{i:.3f}", f"{o:.3f}", escape(p.source))
    console.print(out)
    if md:
        links = "\n".join(f"- {name}: {url}" for name, url in PRICE_LINKS.items())
        md.write_text(f"# Model prices\n\n{price_table_md(rows)}\n\n## Where the prices come from\n\n{links}\n")
        console.print(f"wrote {md}")


def run_cli() -> None:
    load_env_files()
    try:
        app()
    except BannahaError as exc:
        Console(stderr=True).print(f"[bold red]error:[/] {exc}")
        sys.exit(2)


__all__ = ["app", "config_path", "run_cli"]
