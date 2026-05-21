# TokenLeak — Workflow Reference

## Command overview

```
python -m tokenleak scan    [TARGET ...] [--sha SHA] [--report [FILE]] [--no-prefilter] [--noanimation]
python -m tokenleak rescan  [TARGET ...] [--sha SHA] [--report [FILE]] [--no-prefilter] [--noanimation]
python -m tokenleak status
python -m tokenleak mcp
```

## Target formats

| Specifier | Resolves to |
|-----------|------------|
| `https://github.com/user/repo.git` | That single repository |
| `git@gitlab.com:group/repo.git` | That single repository (SSH) |
| `github:username` | All repos of a GitHub user (via API) |
| `gitlab:username` | All repos of a user on the configured GitLab |
| `gitlab:https://host:username` | All repos of a user on a specific GitLab host |
| `gitea:username` | All repos of a user on the configured Gitea |
| `gitea:https://host:username` | All repos of a user on a specific Gitea host |
| `server:https://gitlab.host` | ALL repos on that GitLab server |

Targets can be given directly on the command line or read from `repos.txt`
(path controlled by `TOKENLEAK_REPOS_LIST_PATH`).

## Pre-filter control

### Default (enabled)
Files are pre-screened locally before being sent to the AI:
- Shannon entropy check: tokens with entropy > 4.5 on 20+ character strings
- 25+ regex patterns: AWS, GitHub, GitLab, JWT, private keys, passwords, etc.
- Suspicious file names: `.env`, `id_rsa`, `*.pem`, `*.key`, etc.

Only candidate files reach the AI, reducing token costs significantly.

### Disable pre-filter

Via environment (persistent):
```bash
TOKENLEAK_PREFILTER_ENABLED=false
```

Via CLI (one-shot):
```bash
python -m tokenleak scan --no-prefilter https://github.com/user/repo.git
```

When disabled, the AI receives the full content of every text file and
extracted strings from every binary file. Slower and more expensive,
but will catch anything the regex/entropy patterns might miss.

## Scan vs Rescan

**`scan`** — respects the database: commits whose scan status is `done` are skipped.
Useful for daily cron: only new commits are analysed.

**`rescan`** — ignores the database: all commits are analysed again regardless of
previous status. Useful after updating `agent.md` or the AI model, or to
re-verify a repository from scratch.

## Scanning specific commits

```bash
# Scan only the commit starting with abc123
python -m tokenleak scan https://github.com/user/repo.git --sha abc123

# Force rescan of that commit
python -m tokenleak rescan https://github.com/user/repo.git --sha abc123
```

The SHA can be abbreviated (minimum 4 characters). All commits whose full SHA
starts with the given prefix are matched.

## Large repository handling

Repositories larger than `TOKENLEAK_MAX_REPO_SIZE_MB` (default: 2048 MB) are:
1. Skipped with a `WARNING` log entry
2. Recorded in the database as `skipped_too_large`
3. Reported to Mattermost (if configured)

The size check happens after cloning (using `git count-objects -vH`), so the
repo is briefly downloaded then discarded.

Adjust the limit:
```bash
TOKENLEAK_MAX_REPO_SIZE_MB=500   # skip repos over 500 MB
TOKENLEAK_MAX_REPO_SIZE_MB=0     # never skip (0 = unlimited)
```

## Reports

```bash
# Print Markdown report to stdout after scan
python -m tokenleak scan --report

# Write report to a file
python -m tokenleak scan --report /tmp/report.md

# Include report in cron output
python -m tokenleak scan --report - >> /opt/tokenleak/logs/cron.log 2>&1
```

## Status command

```bash
python -m tokenleak status
```

Outputs a summary table:
```
             TokenLeak Status
Repositories           12
  Scans (done)         47
  Scans (error)         2
  Scans (skipped…)      3
Total alerts           18
Tokens used        42,381
Last scan finished 2026-05-20T02:14:33
```

## MCP server mode

```bash
python -m tokenleak mcp
```

Starts the FastMCP server over stdio. Connect with any MCP client
(Claude Desktop, etc.) to use the agent tools interactively.

## AI provider selection

```bash
# Use OpenAI (default)
TOKENLEAK_AI_PROVIDER=openai
TOKENLEAK_AI_API_KEY=sk-...
TOKENLEAK_AI_MODEL=gpt-4o

# Use Ollama (local)
TOKENLEAK_AI_PROVIDER=ollama
TOKENLEAK_AI_API_URL=http://localhost:11434/v1
TOKENLEAK_AI_MODEL=llama3.1:70b
# (API key is ignored for Ollama)

# Use OpenAI-compatible provider (e.g., Azure, Groq)
TOKENLEAK_AI_PROVIDER=openai
TOKENLEAK_AI_API_KEY=your-key
TOKENLEAK_AI_API_URL=https://your-custom-endpoint/v1
TOKENLEAK_AI_MODEL=your-model-name
```

## Config repository

Store `repos.txt` and/or `agent.md` in a private git repository:

```bash
TOKENLEAK_CONFIG_REPO_URL=https://github.com/your-org/tokenleak-config.git
TOKENLEAK_CONFIG_REPO_TOKEN=ghp_...
TOKENLEAK_REPOS_LIST_PATH=repos.txt        # path inside the config repo
TOKENLEAK_AGENT_MD_PATH=agent.md           # path inside the config repo
```

On each run, the config repo is cloned to a temporary directory, the files
are read, and the clone is deleted before scanning begins.
