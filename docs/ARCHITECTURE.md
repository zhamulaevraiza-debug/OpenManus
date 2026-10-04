# OpenManus Web — Architecture

This document describes how the multi-user web product is built on top of the OpenManus agent
framework: the components, the life of a request, the event stream that drives the UI, the
security model, and how to extend the system with new agents and tools.

Deployment is covered in [DEPLOY.en.md](DEPLOY.en.md) (Russian: [DEPLOY.md](DEPLOY.md)).

## Components

```
 Browser / installed PWA (desktop, Android, iOS)
   │   REST (JSON) + Server-Sent Events, session cookie
   ▼
 Caddy (optional, docker compose --profile https): TLS, compression, no buffering
   │
   ▼
 FastAPI server ── web_main.py → app/web/  (one process, one event loop)
   │  ├─ routes/        auth, conversations, runs (SSE), files, settings, users, system
   │  ├─ runs.py        RunManager: queue, limits, cancellation, ask-human bridge
   │  ├─ events.py      event pipeline: seq numbers, persistence, live fan-out
   │  ├─ db.py/models   SQLAlchemy 2 async + SQLite (WAL)
   │  └─ static.py      the built web UI (web/dist) with SPA fallback
   │
   │  run_task(request, mode, history, attachments)   ← the only core entry point
   ▼
 Core ── app/flow/runner.py
   │  ├─ router.py      auto mode: chat | single agent | team
   │  ├─ planning.py    team mode: plan → steps assigned to agents → final answer
   │  └─ answer.py      final answer (streamed) in the user's language
   ▼
 Agents ── app/agent/registry.py → Manus, Browser, Researcher, Coder, Data Analyst,
   │                               Writer, Sandbox (Daytona)
   ▼
 Tools ── app/tool/  python_execute, bash, str_replace_editor, browser_use, web_search,
                     crawl4ai, data_visualization, ask_human, MCP tools, …
          │
          └─ agent code runs as the unprivileged exec user (app/utils/proc.py)
```

| Directory | Responsibility |
|---|---|
| `web/` | React 19 + TypeScript PWA (Vite, Tailwind CSS). `src/api` (REST client, SSE reader, types), `src/features/*` (chat, runs/activity, files, settings, …), `src/i18n` (ru/en). |
| `app/web/` | HTTP API, authentication, run management, event persistence and streaming, file access, admin settings. Settings come from `OPENMANUS_*` variables (`settings.py`). |
| `app/flow/` | Request orchestration: routing, single-agent runs, team planning, final answers. |
| `app/agent/` | Agent classes and the agent registry. |
| `app/tool/` | Tools the agents call; `net_guard.py` (SSRF protection) and the chart renderer (Node.js) live here. |
| `app/context.py` | Per-run context (`RunContext`) shared by every layer. |
| `app/utils/proc.py` | Sandboxed subprocesses: scrubbed environment, own process group, privilege drop. |
| `app/config.py` | `config.toml` / `mcp.json` loading, environment overrides, atomic saves, reload. |
| `Dockerfile`, `docker-compose.yml`, `deploy/` | Container image, entrypoint, HTTPS proxy, smoke test, operations scripts. |

The CLI entry points (`main.py`, `run_flow.py`, `run_mcp.py`, `sandbox_main.py`) use the same
core without a `RunContext` and behave as before: one global workspace, no event sink, questions
answered on the terminal.

## Run context

Every run executes inside one asyncio task that has a `RunContext` bound through a `ContextVar`
(`app/context.py`):

| Field | Meaning |
|---|---|
| `run_id` | Identifier of the run (events are attributed to it). |
| `workspace` | The conversation's directory; tools resolve relative paths against it. |
| `emit_sink` | Callback receiving `(event_type, data)`; `emit()` never raises. |
| `ask_human` | Coroutine asking the user a question (the web UI's ask-human card). |
| `restrict_to_workspace` | File tools may not leave the workspace (`resolve_in_workspace` raises `WorkspaceViolation`). |
| `usage` | Token counters accumulated by the LLM client (`add_usage`). |

Deeply nested code (a tool, the LLM client, the planner) reaches the run through
`current_run()`, `get_workspace()`, `emit()` and `add_usage()` instead of extra parameters.
Agents are created, run and cleaned up **in the run's task**, so cancellation (Stop button,
timeout, shutdown) propagates into the agent, its tools and their subprocesses.

## Life of a request

1. The user sends a message: `POST /api/conversations/{id}/messages` with the text, a mode and
   optional attachment paths (uploaded earlier into the conversation's `uploads/` folder).
2. The server stores the user message and a `Run` in state `queued`, checks the per-user and
   global limits (429 when exceeded, 409 when the conversation already has an active run) and
   starts an asyncio task.
3. The task waits for a slot of the global semaphore (`OPENMANUS_MAX_CONCURRENT_RUNS`), switches
   the run to `running` (`run.started`, `run.status`), prepares the workspace (owned by the exec
   user), binds a `RunContext` and calls `run_task(...)` from `app/flow/runner.py` with the last
   turns of the conversation as history, under `asyncio.wait_for(…, OPENMANUS_RUN_TIMEOUT)`.
4. `run_task` validates the mode and dispatches:
   - **auto** — `router.route()` makes one tool-forced LLM call that picks `chat`, a single agent
     or `team` (`router.decision`); errors fall back to the Manus agent.
   - **chat** — one streamed LLM call with the history (`answer.delta` … `final`).
   - **agent** (`manus`, `browser`, `researcher`, `coder`, `data_analyst`, `writer`, `sandbox`) —
     a fresh agent is created by its registry factory, seeded with the history, run step by step
     (`agent.*`, `tool.*` events), then a streamed "finalize" call turns the transcript into a
     clean markdown answer in the user's language.
   - **team** — `PlanningFlow` asks the planner for a plan whose steps are tagged with agent keys
     (`plan.created`), runs every step on a fresh agent of that type with the request, history,
     plan status and previous step notes (`plan.step_started`, `plan.step_finished`,
     `plan.updated`), marks failed steps `blocked` instead of looping, and writes the final
     answer from all step notes.
5. `run_task` emits `usage` and returns the final markdown. The server stores it as the assistant
   message and emits `run.finished`. Errors, timeouts and cancellations also end with an assistant
   message (a short explanation, or "⏹" for a stopped run) and `run.finished`.

If an agent calls the `ask_human` tool, the server's bridge stores a pending question, switches
the run to `waiting_input` and emits `human.question`. The UI shows an answer card (also after
reopening the conversation); `POST /api/runs/{id}/answer` resolves the question (`human.answer`)
and the run continues. Unanswered questions time out after `OPENMANUS_HUMAN_INPUT_TIMEOUT`.

Run states: `queued → running ⇄ waiting_input → completed | failed | cancelled`. Runs left
active by a crash or restart are marked `failed` ("Server restarted") on startup.

## Events

Core code reports progress with `emit(type, **data)`. The server's event pipeline
(`app/web/events.py`) assigns a per-run, monotonically increasing `seq`, adds `ts` and `run_id`,
replaces screenshots (`image_b64`) by stored artifacts (`image_url`), persists events in batches
and broadcasts them to subscribers.

| Type | Data | Emitted by |
|---|---|---|
| `run.started` / `run.status` / `run.finished` | mode / status / status, error, duration_ms, usage | server |
| `router.decision` | mode, agent, reason | router |
| `agent.started` | agent, title, max_steps | agent |
| `agent.step` | agent, step, max_steps | agent |
| `agent.thought` | agent, step, content | tool-calling agent |
| `tool.call` | agent, step, call_id, name, arguments | tool-calling agent |
| `tool.result` | agent, step, call_id, name, output (≤ 8000 chars), error, image_b64 / image_url | tool-calling agent |
| `agent.stuck` | agent, step | agent |
| `agent.finished` | agent, steps, reason | agent |
| `plan.created` / `plan.updated` | plan_id, title, steps[{index, text, agent, status, notes}] | team flow |
| `plan.step_started` / `plan.step_finished` | index, agent, text / status, summary | team flow |
| `human.question` / `human.answer` | question_id, question / answer | server bridge |
| `sandbox.ready` | vnc_url, website_url | Daytona sandbox agent |
| `workspace.changed` | paths | server (after each tool result) |
| `answer.delta` / `final` | content | runner |
| `usage` | input_tokens, completion_tokens | runner |
| `log` | level, message | any |

**Delivery to the browser.** `GET /api/runs/{id}/events?after=<seq>` is a Server-Sent Events
stream: it replays persisted events with `seq > after` (or `Last-Event-ID`), then streams live
events, sends a `: ping` comment every 15 s and closes after `run.finished`. Subscriber queues
are bounded; a slow client is dropped and resumes from its last `seq`. The web client reads the
stream with `fetch` (cookies, reconnect with exponential backoff, resume from the last `seq`,
resubscribe on visibility/online changes) and falls back to polling
`GET /api/runs/{id}/events.json?after=<seq>` after repeated failures. Persistence is capped at
5000 events per run; above the cap only essential events (run, plan, human, final, usage) are kept.

Because the stream is resumable by `seq`, the UI can reload, switch devices or lose the network
without losing progress, and finished runs replay their full activity.

## Data layout

`OPENMANUS_DATA_DIR` (`/data` in the container):

```
/data                      0711 root   traversable, not listable
├── openmanus.db           0600        users, conversations, messages, runs, events, settings
├── secret.key             0600        session signing key (unless OPENMANUS_SECRET_KEY)
├── initial_admin_password.txt  0600   until deleted by the admin
├── config/                0700        config.toml (model, search, browser, team), mcp.json
├── logs/                  0700        rotating application logs
├── artifacts/<run_id>/    0700        screenshots referenced by events
├── tmp/uploads/           0700        upload staging
└── workspaces/            0711 root
    └── <user_id>/         0711 root
        └── <conversation_id>/   exec user   the conversation's files (uploads/, agent output)
```

Identifiers are random UUID4 hex strings, so workspace paths cannot be guessed.

## Security model

- **Authentication:** bcrypt password hashes; an HS256 JWT in the `om_session` cookie (httpOnly,
  SameSite=Lax, Secure on HTTPS) carrying the user id and a password version, so a password
  change or disabling an account revokes its sessions. Failed logins are throttled per IP and
  user name. Mutating requests must be JSON or multipart, and a present `Origin` must match the
  host (or `OPENMANUS_CORS_ORIGINS`).
- **Authorization:** users only see their own conversations, runs and files (others answer
  404). Settings and user management are admin-only. Self-registration is off by default.
- **Browser hardening:** strict Content-Security-Policy and security headers on the app;
  user files of type HTML/SVG are served with `Content-Security-Policy: sandbox …` (opaque
  origin, no access to the session) and previewed in sandboxed iframes.
- **Agent code isolation:**
  - The container runs the server as root with only `SETUID, SETGID, CHOWN, FOWNER,
    DAC_OVERRIDE, KILL` capabilities, `no-new-privileges` and a PID limit.
  - Every subprocess that executes model-written code (`python_execute`, `bash`, the chart
    renderer, editor helpers) is started through `app/utils/proc.py`: in the workspace, in its
    own session/process group (killed as a whole on timeout or cancellation), with a minimal
    environment that never contains `OPENMANUS_*` settings, API keys, tokens or cloud
    credentials, and — when the server runs as root — as `OPENMANUS_EXEC_USER` (`agent`,
    uid 10001 in the image).
  - The exec user owns the conversation workspaces and nothing else: the data directory is
    traversable but not listable, and the database, configuration, keys, logs and artifacts
    are private to root. The entrypoint re-applies these permissions on every start and hands
    workspaces to the exec user without following symlinks or touching hard-linked files.
  - File tools are confined to the workspace (`resolve_in_workspace`, symlinks resolved).
  - Browser and fetch tools pass every URL through `app/tool/net_guard.py`: only http(s), and no
    private, loopback, link-local or metadata addresses (including IPv4-mapped/6to4/NAT64 forms)
    unless `OPENMANUS_ALLOW_PRIVATE_NETWORK=true`.
  - Limits: concurrent runs (global and per user), run timeout, browser slots, upload size,
    bounded tool outputs and event payloads.
- **Known limits:** all conversations' code runs as the same exec user, separated by
  unguessable paths rather than OS permissions; code started by agents has general network
  access. OpenManus is meant for trusted users.

## Extending OpenManus

### Adding an agent

1. **Prompt** — add `app/prompt/<name>.py` with `SYSTEM_PROMPT` (use a `{directory}` placeholder
   for the workspace) and `NEXT_STEP_PROMPT`.
2. **Agent class** — subclass `ToolCallAgent` (see `app/agent/writer.py`). Create everything
   per instance with `default_factory`, never as shared class attributes:
   ```python
   class TranslatorAgent(ToolCallAgent):
       name: str = "translator"
       description: str = "Translates documents in the workspace"
       system_prompt: str = Field(
           default_factory=lambda: SYSTEM_PROMPT.format(directory=get_workspace())
       )
       next_step_prompt: str = NEXT_STEP_PROMPT
       available_tools: ToolCollection = Field(
           default_factory=lambda: ToolCollection(StrReplaceEditor(), Terminate())
       )
       special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])
   ```
   If the agent holds resources (browsers, sessions), release them in `cleanup()`.
3. **Registry** — add a factory and an entry to `_SPECS` in `app/agent/registry.py`:
   ```python
   async def _create_translator(kwargs: dict) -> BaseAgent:
       from app.agent.translator import TranslatorAgent

       return TranslatorAgent(**kwargs)

   _spec(
       "translator",                       # key: mode name, event attribution, plan tags
       "Translator",                       # display name (English fallback)
       "Translates documents between languages, keeping the formatting",  # seen by router & planner
       "languages",                        # lucide icon name
       _create_translator,
       is_available=_always_available,     # or a check returning (False, "reason")
       team_member=True,                   # may the planner assign steps to it?
   )
   ```
   The router, the team planner, `GET /api/agents` and the mode picker pick it up automatically.
   Optionally add the module to `_CORE_MODULES` in `app/web/runs.py` so it is preloaded at startup.
4. **Web UI** — add the localized name and description under `agents.<key>` in
   `web/src/i18n/ru.ts` and `en.ts` (the API text is the fallback) and, for a nicer icon, map the
   key in `ICONS_BY_KEY` / the icon name in `ICONS_BY_NAME` in `web/src/features/agents/meta.ts`.
5. **Tests** — cover the factory and a scripted run with a fake LLM (see `tests/core/`).

### Adding a tool

1. Subclass `BaseTool` (`app/tool/base.py`) with `name`, `description`, a JSON-schema
   `parameters` object and `async def execute(self, **kwargs) -> ToolResult`. Return
   `ToolResult(output=…)`, `ToolResult(error=…)` for user-facing failures, and
   `base64_image=…` for a screenshot (the UI shows it as a thumbnail).
2. Follow the run contract:
   - resolve every path with `resolve_in_workspace(path)` and write outputs into `get_workspace()`;
   - start processes only through `app/utils/proc.py` (`run_process(...)` or
     `subprocess_kwargs(...)`), never with a plain `subprocess` call;
   - check URLs with `net_guard.check_url(url)` before fetching them;
   - keep `execute` cancellation-safe (clean up in `finally`, kill process groups on
     `CancelledError`) and bound its output;
   - ask the user with `current_run().ask_human` when a run is active (see `app/tool/ask_human.py`).
3. Add the tool to the `ToolCollection` of the agents that should use it.
4. Optionally give it an icon and label in `web/src/features/runs/tools.ts`.
5. Add tests under `tests/tools/` using the `run_ctx` fixture (an active `RunContext` with a
   temporary workspace).

MCP servers configured in Settings → MCP servers (stored in `mcp.json`) add tools to the Manus
agent without code changes.

## Testing and CI

| Suite | Command | Covers |
|---|---|---|
| Core | `pytest tests/core` | config, LLM client, agents, flows, runner (fake LLM) |
| Tools | `pytest tests/tools` | tools, sandboxed processes, SSRF guard, browser and chart renderer |
| Web API | `pytest tests/web` | auth, conversations, runs, SSE, files, admin, static files |
| Fake LLM | `pytest tests/fake_llm` | the scripted OpenAI-compatible model used by e2e tests; the real core (router, agents, team, tools, browser) running against it over HTTP |
| Deployment scripts | `pytest deploy/tests` | password reset, tiktoken cache preparation |
| Docker sandbox | `pytest tests/sandbox` | the CLI's Docker sandbox (needs a Docker daemon) |
| Web UI | `cd web && npm run lint && npm run typecheck && npm test` | components, SSE client, i18n |
| End-to-end | `scripts/e2e.sh` (Playwright specs in `web/e2e/`) | the real server and UI with the fake LLM (`tests/fake_llm`): sign-in, chat, agent and team runs, ask-human, stop/retry/reload, files, settings, users, PWA — desktop and phone; `--screenshots` regenerates `docs/screenshots` |
| Image | `deploy/smoke-test.sh openmanus-web:latest` | built image: health, first run, permissions, privilege drop, browsers |

`.github/workflows/ci.yml` runs the Python suites (Docker-marked tests excluded), the web UI
checks and build, the end-to-end suite, and builds the Docker image (not pushed, BuildKit cache in GitHub Actions)
followed by the smoke test. Workflows that need the upstream project's secrets (PyPI publishing,
PR summaries) or manage its issues only run in `FoundationAgents/OpenManus`.
