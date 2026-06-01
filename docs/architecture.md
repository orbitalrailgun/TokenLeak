# TokenLeak — Architecture

## Overview

TokenLeak is a Python application that scans git repositories for leaked secrets,
tokens, passwords, PII, and corporate-sensitive information using an AI agent.

```
CLI (python -m tokenleak)
    │
    ├── lock.py          — PID file prevents concurrent instances
    ├── config.py        — All settings from environment variables
    │
    ├── providers/       — Enumerate target repositories
    │   ├── github.py    — GitHub REST API
    │   ├── gitlab.py    — GitLab REST API (self-hosted + gitlab.com)
    │   ├── gitea.py     — Gitea / Forgejo REST API
    │   └── generic.py   — Validate plain git URLs
    │
    ├── scanner/         — Local analysis of cloned repos
    │   ├── clone.py     — Safe git clone (no hooks, no exec bits)
    │   ├── walker.py    — Walk full git commit history; extract images/notebooks
    │   ├── extractor.py — Read text files; extract strings from binaries
    │   ├── prefilter.py — Shannon entropy + regex patterns (optional)
    │   └── ocr.py       — Vision-model analysis of images and Jupyter notebooks
    │
    ├── mcp_server/
    │   └── server.py    — FastMCP server with agent tools
    │
    ├── agent/
    │   ├── client.py    — Unified OpenAI / Ollama API client
    │   └── runner.py    — Two-pass agent loop orchestration
    │
    ├── notifications/
    │   └── mattermost.py — Optional Mattermost alerts
    │
    ├── report/
    │   └── markdown.py  — Markdown report generation
    │
    ├── animation.py     — Live token counter animation (Rich)
    ├── logging_setup.py — stderr + syslog handler setup
    └── db/
        ├── base.py      — Abstract DB interface + shared DDL
        ├── sqlite.py    — SQLite implementation
        └── postgres.py  — PostgreSQL implementation
```

## Component responsibilities

### CLI / `__main__.py`
- Parses CLI arguments (`scan`, `rescan`, `status`, `mcp`)
- Acquires the process lock before any scan
- Applies CLI flag overrides to Config (--no-prefilter, --noanimation, --report)
- Delegates to `cli.py` command functions

### Config
All settings come from environment variables (via `.env` file or shell).
No settings are hardcoded. The `Config` dataclass is a singleton
accessed via `get_config()`.

### Process lock (`lock.py`)
- Writes PID to `TOKENLEAK_LOCK_FILE` on start
- Checks if the existing PID is alive before raising an error
- Cleans up stale locks from crashed processes
- Registers SIGTERM/SIGINT handlers to clean up on exit

### Providers
Each provider implements a generator that yields plain `https://` clone URLs.
The `__init__.py` dispatcher handles the target format:

| Format | Provider |
|--------|----------|
| `https://github.com/user/repo.git` | generic pass-through |
| `github:username` | GitHub API |
| `gitlab:username` | GitLab API (configured URL) |
| `gitlab:https://host:username` | GitLab API (custom host) |
| `gitea:username` | Gitea API |
| `server:https://gitlab.host` | All repos on GitLab server |

### Scanner

**clone.py** — Safe git clone:
- `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=echo` prevent auth prompts and fail-fast
- `.git/hooks/` is wiped immediately after clone
- All execute bits removed recursively from the working tree
- Timeout enforced via `subprocess.run(timeout=…)`
- Clone deleted from disk after scan (`remove()`)

**walker.py** — Git history walk:
- `git log --all` retrieves all commits across all branches
- Per-commit: `git diff-tree --root` identifies changed files; `--root` ensures root
  commits (no parent) are treated as diffs from an empty tree so their files are included
- `git show SHA:path` extracts file content at a specific commit
- `list_branch_tips()` enumerates remote-tracking refs (`refs/remotes/*`) and returns one
  `CommitInfo` per unique tip SHA, excluding already-done commits
- `checkout_detach(sha)` / `checkout_previous()` temporarily switch the working tree
  for branch-tip full scans; always wrapped in try/finally

**extractor.py** — Content extraction:
- Text files decoded as UTF-8 with error replacement
- Binary files: printable ASCII runs ≥ 6 chars extracted (strings-equivalent)
- Files > `max_file_size_mb` are skipped

**prefilter.py** — Optional local filter (default: enabled):
- Shannon entropy > 4.5 on tokens ≥ 20 chars → candidate
- 25+ regex patterns: AWS keys, GitHub tokens, JWTs, private keys, passwords,
  connection strings, Slack, Stripe, Twilio, Google API, etc.
- Suspicious file names: `.env`, `id_rsa`, `*.pem`, `*.key`, etc.
- When disabled (`TOKENLEAK_PREFILTER_ENABLED=false` or `--no-prefilter`),
  every file is sent to the AI

### FastMCP Server (`mcp_server/server.py`)
Defines the tools the AI agent can call. File paths received from the agent in `save_alert()`
are normalized before storage: Unicode confusable characters (non-breaking hyphens, en/em
dashes, fullwidth variants, etc.) are replaced with their ASCII equivalents and the string is
NFC-normalized, with a `WARNING` log entry when a change occurs.

| Tool | Purpose |
|------|---------|
| `save_alert` | Persist a finding to the database |
| `save_note` | Save an intermediate analysis note |
| `get_notes` | Read previously saved notes |
| `read_file` | Read file from current HEAD |
| `read_file_at_commit` | Read file at a historical commit |
| `list_files` | List files with optional glob |
| `search_content` | grep across the repo |
| `get_commit_log` | Get git log text |
| `get_file_tree` | Get file tree at HEAD |
| `send_mattermost` | Send notification to Mattermost |
| `analyze_image_file` | OCR-scan an image file or Jupyter notebook for sensitive data |

The server can run in two modes:
1. **Embedded**: tool functions called directly from `agent/runner.py` via `TOOLS` dict
2. **Standalone**: `python -m tokenleak mcp` starts the FastMCP stdio server

### Agent (`agent/client.py`)

`build_client()` returns a unified `OpenAI` client for both OpenAI-compatible providers and
Ollama. The `chat()` function wraps all API calls and raises typed exceptions instead of
propagating raw API errors:

- `InsufficientFundsError` — billing / quota exhaustion; fatal, stops scanning immediately
- `ContextWindowExceededError` — conversation history grew beyond the model's context window;
  non-fatal: the agent loop catches it, preserves all alerts saved so far, and moves on to the
  next commit

### Agent (`agent/runner.py`)

Two scan modes are provided:

**Diff mode** — used for individual commits in `scan`:
- Extracts only the added lines of the commit via `git show --unified=0`
- Pre-filters lines with entropy + regex; sends candidates to the AI in a single pass
- Agent calls `save_alert()` directly — no read_file loop needed
- Agent can still call `read_file()` for surrounding context

**Full mode** — used for HEAD in `scan` (first run) and `rescan`:

*Pass 1 — Map*
- Input: file tree + commit log (up to 200 entries)
- Agent identifies high-risk areas and saves a risk map via `save_note()`
- No alerts saved in this pass

*Pass 2 — Deep Scan*
- Agent reads its Pass 1 notes, then reads individual files
- For each confirmed finding: calls `save_alert()`
- Uses `read_file_at_commit()` for deleted historical files
- Loop runs until agent returns a message with no tool calls, or `ai_max_iterations`

**OCR pass** (when `TOKENLEAK_OCR_MODEL` is set):
- Runs after the diff/full scan pass
- Sends all images and Jupyter notebook outputs to the vision model
- Findings saved directly to the database without an agent loop

Token counts are accumulated from API responses and stored in the `scans` table.
Billing errors (`InsufficientFundsError`) stop scanning immediately and surface a
clear message to the user.

### Database

Two implementations with identical interface (`Database` ABC):

- **SQLite** — default, zero-config, uses WAL mode for concurrency safety
- **PostgreSQL** — selected via `TOKENLEAK_DB_TYPE=postgres`, uses `psycopg2`

Schema:

```
repos   — known repositories
scans   — one row per (repo, commit_sha, ai_model); tracks status, scan_mode, and token usage
alerts  — findings with agent JSON payload, commit context, and triggered_by label
notes   — agent's intermediate notes per scan
```

The `scans` table has a `UNIQUE(repo_id, commit_sha, ai_model)` constraint, so multiple
models can each hold their own scan rows for the same commit in the same database. This is
the foundation of multi-model comparison without separate DB files.

Key fields:
- `scans.scan_mode` — `"full"` or `"diff"`, records how a commit was processed
- `scans.ai_model` — which model produced this scan row
- `alerts.repo_id`, `alerts.commit_sha`, `alerts.commit_date` — links alert to its origin commit
- `alerts.triggered_by` — `"scan"` or `"rescan"`, records which command generated the alert
- `alerts.ai_model` — which model produced this alert

Key API additions:
- `list_scans(repo_id, ai_model=None)` — filter scans by model; used by `done_shas` logic so a second model never skips commits already scanned by the first
- `list_alerts_for_repo(repo_id, ai_model=None)` — return all alerts for a repo, optionally filtered by model; avoids joining individual scan IDs for cross-model queries
- `get_scan(repo_id, commit_sha, ai_model=None)` — returns the model-specific scan row, or the most recent one if `ai_model` is not given

Both SQLite and PostgreSQL implementations apply migrations automatically on startup —
no manual schema changes are needed when upgrading. The constraint migration from the
older `UNIQUE(repo_id, commit_sha)` runs automatically: SQLite recreates the table,
PostgreSQL uses `ALTER TABLE DROP CONSTRAINT / ADD CONSTRAINT`.

## Data flow

```
1. CLI parses args
2. Acquire PID lock
3. Collect targets → resolve to git URLs (providers API)
4. For each URL:
   a. git clone (safe)
   b. Check repo size → skip if too large (log + Mattermost)
   c. git log → list all commits (skip merge commits)
   d. Determine scan strategy:
      - First run or rescan:
          i.   Full scan of HEAD (Pass 1 + Pass 2 + OCR)
          ii.  Full scan of every other remote branch tip (Pass 1 + Pass 2 + OCR),
               each with git checkout --detach / checkout - around the scan
          iii. Diff scan of every historical commit across all branches
      - Subsequent scan:
          i.  Diff scan of commits newer than last successful scan only
   e. For each scan:
      i.   Create scan row (status=pending, scan_mode=full|diff)
      ii.  Run agent → alerts/notes written to DB (triggered_by=scan|rescan)
      iii. Run OCR pass if TOKENLEAK_OCR_MODEL is set
      iv.  Update scan status (done/error)
      v.   Generate report if --report
      vi.  Send Mattermost summary
   f. Delete clone from disk (try/finally — always runs)
5. Release PID lock
```

If the AI API returns a billing error at any point, scanning stops immediately
and the user sees a clear "API funds exhausted" message.

## Security considerations

- Cloned repos are treated as hostile — no code execution, no git hooks
- File access is read-only; no shell interpretation of file contents
- Agent is instructed never to verify or use found credentials
- Clone directory is cleaned up regardless of scan outcome (try/finally)
- Database credentials stored only in environment variables, never in code
- PostgreSQL configured with least-privilege role (see `docs/postgresql_setup.md`)
