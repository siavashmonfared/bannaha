# bannaha

A terminal sandbox for multi-agent coordination. Give a fleet of LLM agents a question, pick how they
coordinate, and watch it happen live: who searches what, which claims get posted, endorsed or challenged,
how the group reaches an answer, and what happens when part of the fleet goes dark.

- **Any harness, any backend.** Mix agents built on different agent loops (Anthropic SDK, OpenAI Agents
  SDK, Google ADK, Hermes Agent, banna) reaching models through different routes (direct API, OpenRouter,
  Amazon Bedrock) in one fleet.
- **Pluggable strategies.** A single-agent baseline, independent voting, and a shared blackboard where
  peers cross-check evidence until they agree. Adding one is one file.
- **Follow-ups with memory.** When a run ends, keep asking about it; the answers come from an agent that
  has the whole run as memory.
- **Same tools everywhere.** One tool spec, translated per harness: web search, page fetch, document
  search, sandboxed Python, and a set of communication tools for the shared board.
- **Fault injection.** Drop a fraction of the fleet mid-run and compare how strategies cope.
- **Everything is an event.** The live view and the JSONL run log are subscribers to one event stream.

## Install

```bash
uv tool install "bannaha[all]"        # or: pipx install "bannaha[all]"
```

From a clone:

```bash
git clone https://github.com/siavashmonfared/bannaha && cd bannaha
uv sync --all-extras
uv run bannaha --help
```

Extras: `anthropic`, `openai` (OpenAI Agents SDK), `adk` (Google ADK), `hermes` (Hermes Agent), `banna`,
`litellm` (ADK and OpenAI-SDK access to OpenRouter/Bedrock models), `bedrock` (boto3 + LiteLLM), `all`.
Install only what you use; `hermes` in particular pulls in a large dependency tree.

## Quickstart

No API keys needed for a first look; the `mock` harness is a scripted offline agent whose model field
is the answer it gives:

```bash
bannaha run "What is the answer?" -s blackboard -f "mock@local:42*2,mock@local:41*2,mock@local:40" --no-web
```

With real models (keys in the environment or a `.env` file, see [API keys](#api-keys)):

```bash
bannaha run "Which element has the highest melting point, and what is it in kelvin?" \
  -s blackboard -f "anthropic@direct:claude-haiku-4-5*3,openai@openrouter:google/gemini-2.5-flash*2"
```

## API keys

Put keys in `~/.config/bannaha/.env` (read from any directory, and never inside a project you might
commit), in a `.env` in the directory you run from, or in your shell environment. Variables already set
in the environment win; empty values are ignored. `.env.example` lists every variable.

```bash
ANTHROPIC_API_KEY=...     # anthropic@direct, banna@direct:anthropic/...
OPENAI_API_KEY=...        # openai@direct, hermes@direct, banna@direct:openai/...
GEMINI_API_KEY=...        # adk@direct, banna@direct:gemini/..., and web search
OPENROUTER_API_KEY=...    # any harness @openrouter
AWS_REGION=us-east-1      # any harness @bedrock, plus AWS credentials: AWS_PROFILE,
AWS_PROFILE=default       # or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, or `aws sso login`
```

`bannaha list` reports whether the web search key was found, and every run checks the keys its fleet
needs before starting.

## How a run is shown

Runs are shown as one continuous transcript, in time order, the way Claude Code and Codex show work:
a header when the speaking agent changes, one line per finished tool call with a short preview of its
result, answers, proposals and votes in full, round boundaries and decisions, and a
live status line at the bottom with what each agent is doing, the round, tokens and cost.

- `bannaha run` prints the transcript into the terminal, so it stays in your
  scrollback. `--full` shows tool results in full; `--dashboard` gives the boxed dashboard instead;
  `--plain` prints one line per event.
- In the full-screen app (Run page, and replays on the Runs page): `ctrl+o` expands or shortens tool
  results, `ctrl+g` follows one agent at a time, `ctrl+t` switches between the transcript and the dashboard.

## Follow-ups: ask about a finished run

When a run finishes, what you type next is a follow-up question about it, not a new task. It goes to one
agent, by default the strongest model in the fleet (the one with the highest per-token price), together
with the run's memory: the task, how the strategy decided, what every agent submitted, the key tool
results, and a `search_run` tool over the full trace. The agent can also use web search and Python to
check things. The conversation builds turn by turn and is saved with the run (`followups.jsonl`).

| command | what it does |
|---|---|
| plain text | after a run: a follow-up question about it; before any run: a new task |
| `/new <task>` | a fresh run that knows nothing of the current one |
| `/rerun <clarification>` | the same strategy and fleet again, told the previous answer and your clarification |
| `/to <agent>` | send follow-ups to another fleet agent (`a3`) or any model (`harness@backend:model`) |
| `/agents` | the fleet, and who answers follow-ups |
| `/forget` | clear the follow-up conversation (the run's memory stays) |
| `/help` | all commands and keys |

This works on the Run page of the app, after `bannaha run` in the terminal (an `›` prompt; `--no-followup`
skips it), and on any saved run (`a` on the Runs page, or `bannaha ask <run folder | last> ["question"]`).

## The app

Run `bannaha` with no arguments to open the full-screen app. Number keys switch pages:

| page | what it does |
|---|---|
| **1 Setup** | the settings dashboard: ↑↓ move, ←/→ toggle a value in place, ↵ open a picker; `s` start, `d` save as default and start. Shows whether the setup is ready to run, and what is missing if not. **Limits** (↵) edits tool calls and model calls per turn, output tokens per reply, wall time, tokens per run, and cost caps per run and per agent |
| **2 Fleet** | the fleet as a table of agent groups with each group's key status; `a` add, `e`/↵ edit, `x` remove, `+`/`-` count |
| **3 Keys** | every API key variable: found or missing, where it was found, and which ones your current fleet needs; `e` opens `~/.config/bannaha/.env` in your editor, `r` reloads |
| **4 Run** | type a question in the `›` box and press Enter; agents (with live tokens and cost), board activity, rounds and the answer update live, and the **activity** panel shows, per agent, the tool call it is running and its last few steps (searches, pages, code, messages, errors). Esc cancels a run, ↑↓ recall earlier questions |
| **5 Runs** | every saved run; ↵ replays a run's rounds, board and answer |

The animated header (a torus and the crew member who builds on it) sits under the page bar; `ctrl+b`
hides or shows it on any page, and Setup → Banner saves the choice.

Settings saved with `d` go to `~/.config/bannaha/config.toml`. `bannaha run …` is the scriptable path
with no full-screen app; flags override the saved settings for that run.

## Limits and cost

| limit | default | `bannaha run` flag |
|---|---|---|
| tool calls per agent turn | 16 | `--max-calls` |
| model calls per agent turn | 24 | – |
| output tokens per model reply | 16384 | – |
| wall time per run | 900 s | `--max-wall` |
| tokens per run | none | `--max-tokens` |
| cost per run (USD) | none | `--max-cost` |
| cost per agent (USD) | none | `--max-agent-cost` |

Defaults come from the Setup page (saved settings); flags override them for one run. Cost is metered
live at list prices (`bannaha prices`): each agent's model tokens plus its web searches (Gemini search
tokens and $0.035 per search). When the run reaches `--max-cost` it stops at the next tool call;
an agent that reaches `--max-agent-cost` ends its turn and does no more work while the others carry on.
The Anthropic, OpenAI Agents and ADK harnesses report tokens after every model call, so their limits
apply mid-turn; Hermes and banna report at the end of a turn, so their spend is checked between turns.
`result.json` records each agent's tokens, searches and cost, and the run total.

Prices come from OpenRouter's public model list, which also lists OpenAI, Anthropic and Google models,
so it serves as the list price for direct and Bedrock runs too; pin anything else in
`~/.config/bannaha/prices.toml` (`["model"]` with `input_per_mtok` and `output_per_mtok`). `bannaha
prices` shows what your fleet will be charged, and `bannaha prices --md prices.md` writes it as a table
with the pricing links. Caching, batch discounts and long-context surcharges are not modeled.

## Fleets

A fleet is `harness@backend:model*count`, comma separated. Count defaults to 1.

| harness | backends | notes |
|---|---|---|
| `anthropic` | `direct`, `bedrock` | Anthropic SDK, Messages API tool-use loop |
| `openai` | `direct`, `openrouter`, `bedrock` | OpenAI Agents SDK; tracing disabled; Bedrock via LiteLLM |
| `adk` | `direct` (Gemini), `openrouter`, `bedrock` | Google ADK; non-Gemini models via LiteLLM |
| `hermes` | `openrouter`, `direct` (OpenAI), `local` | Nous Research Hermes Agent; `local` is any OpenAI-compatible server at `BANNAHA_HERMES_BASE_URL` |
| `banna` | `direct`, `openrouter`, `bedrock` | banna's ReAct policy; on `direct` the model is `vendor/model` (`anthropic/…`, `openai/…`, `gemini/…`) |
| `mock` | `local` | offline scripted agent for demos and tests |

| backend | needs |
|---|---|
| `direct` | the vendor's key: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `bedrock` | `AWS_REGION` and AWS credentials (profile, keys or SSO); model ids such as `us.anthropic.claude-haiku-4-5-20251001-v1:0` |

`bannaha list` shows what is installed. The fleet is checked before anything runs: unsupported
harness/backend pairs, missing packages, keys and AWS credentials are reported up front.

Every harness gets the same tools from the same spec, and a conformance test checks each adapter
(nested arguments arrive unchanged, the turn stops at the finishing tool, a dropped agent really stops).
Some notes on how the adapters work:

- **Hermes**: bannaha's tools are registered in-process in Hermes's tool registry and only the current
  turn's tools are enabled, so Hermes's own terminal, browser and file tools are never offered. Hermes
  keeps state in a private home (`BANNAHA_HERMES_HOME`, default `~/.cache/bannaha/hermes`) with memory,
  context files and persona loading off; your own `~/.hermes` is not read or written.
- **banna**: banna's loop has its own final-answer step; if a model answers that way instead of calling
  the turn's finishing tool, the answer is submitted through that tool.
- **ADK**: tools declare their full JSON Schema, and instructions are passed so ADK does not treat
  braces in a prompt as template variables.

## Strategies

| strategy | how it works | reference |
|---|---|---|
| `single` | one agent answers alone; the baseline | – |
| `vote` | agents answer independently and never see each other; the most common answer wins | self-consistency, Wang et al. 2022 |
| `blackboard` | peers research, post claims with evidence to a shared board, endorse or challenge each other, and propose answers; commit when one answer reaches quorum, otherwise revise; if no quorum by the last round, rank the candidates in a ballot | cluster → quorum → revise, Borda ballot |

**`blackboard` in detail.** Each round, every live agent reads the shared board, researches, posts the
facts it establishes as claims (value, short verbatim quote, source), endorses, challenges or
corroborates its peers' claims, and proposes an answer that cites its supporting claims. Proposals are
clustered (numbers within `tolerance`, text after normalization). If the top cluster has `quorum` of the
live agents (and at least `min_quorum`), the group commits. Otherwise everyone sees the candidates with
the evidence behind each and revises. After the last round with no quorum, agents rank the candidates in
a ballot (Borda count); `fallback=plurality` then commits the leader, `fallback=fail` reports a failure.
Proposals carry the board version the agent last read, and a proposal made on a stale board is sent
back once so the agent can catch up.

Options are set with `-o key=value`, for example `-o rounds=4 -o quorum=0.6 -o fallback=fail`.
`bannaha list strategies` shows every option and its default.

## Tools

| pack | tools |
|---|---|
| research | `web_search` (Gemini with Google Search grounding), `fetch_page`, `search_documents`, `run_python` |
| board | `read_board`, `post_claim`, `endorse_claim`, `challenge_claim`, `corroborate`, `send_message`, `read_messages` |
| finishing | `final_answer` (single, vote), `propose_answer` and `cast_vote` (blackboard), `reply` (follow-ups) |

Each strategy decides which tools an agent gets. `--no-web` removes the web tools; `--no-python`
removes `run_python`. Web search needs `GEMINI_API_KEY` even when no agent uses a Gemini model.

`run_python` runs in a host subprocess by default (`--sandbox process`): it has a wall-time limit but
shares your filesystem, network and credentials. Use `--sandbox docker` for a throwaway container with
no network, a read-only root filesystem, dropped capabilities and resource caps.

Anything one agent writes and another reads (board entries, messages, peer updates) is sanitized,
length-capped and labelled as peer data, never as instructions.

## Features

- **Peer updates** (`--peer-updates`, on by default for board strategies): every tool result carries the
  peers' new board activity since the agent last looked, so agents stay aware of each other mid-turn.
- **Dropout** (`--drop 0.5 --drop-after 2 --seed 1`): silently remove agents mid-run. `--drop` below 1
  is a fraction, otherwise a count; a chosen agent goes dark when it tries tool call number
  `drop-after + 1`.

Example: how much does independent voting lose, compared with the blackboard, when half the fleet goes dark?

```bash
Q="Which five rivers are the longest in the world, and how long is each in km?"
F="anthropic@direct:claude-haiku-4-5*7"
bannaha run "$Q" -s vote       -f "$F" --drop 0.5 --seed 7
bannaha run "$Q" -s blackboard -f "$F" --drop 0.5 --seed 7
```

## Runs

Every run is saved to `~/.local/share/bannaha/runs/<timestamp>-<strategy>/` (override with
`--runs-dir` or `BANNAHA_RUNS_DIR`, disable with `--no-save`):

- `events.jsonl`: every event, one per line
- `result.json`: the question, fleet, options, outcome and per-agent stats
- `followups.jsonl`: the follow-up conversation, if any

`--plain` prints one line per event instead of the live view; `--json` prints the outcome as JSON.

## Extending

**A strategy** is a module in `bannaha/strategies/` (or any package, via the `bannaha.strategies` entry
point):

```python
from bannaha.core.outcome import Outcome
from bannaha.core.registry import register_strategy
from bannaha.core.session import TurnJob
from bannaha.strategies.base import Strategy
from bannaha.tools.toolbox import RESEARCH

@register_strategy
class FirstAnswer(Strategy):
    name = "first"
    summary = "Everyone answers; the first finished answer wins."

    def run(self, session):
        tools = session.tools(*RESEARCH, "final_answer")
        results = session.run_turns([TurnJob(a, "Answer the question.", session.question, tools, "answer")
                                     for a in session.alive()])
        done = [r for r in results if r.finished]
        return Outcome("committed", done[0].payload["answer"]) if done else Outcome("no_answer")
```

Built-ins are discovered by importing every module in their package, so adding or removing a strategy,
harness or feature is adding or deleting a file.

**A harness** subclasses `bannaha.harnesses.base.Harness`, declares its backends and required
environment variables, and implements `run_turn(req)`: drive the model, call `req.call_tool(tool, args)`
for every tool call, stop when `req.is_done()`. `tests/test_harness_conformance.py` checks that nested
arguments arrive unchanged and that control signals end the turn.

**A feature** subclasses `bannaha.features.base.Feature` and hooks `before_tool` / `after_tool`.

## Roadmap

Ideas, not commitments: an MCP server so external agents can join a fleet; a batch runner for sweeping
strategies × fleets × dropout over a question set; more coordination strategies.

## License

MIT
