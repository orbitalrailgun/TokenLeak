# TokenLeak

AI-powered git repository security scanner. Detects leaked secrets, API tokens,
passwords, PII, and corporate-sensitive information across the full commit history
using an AI agent with MCP tools.

---

## Features

- **Full history scan** — analyses every commit, not just the current state
- **AI agent** — two-pass: risk map first, then deep file-by-file analysis
- **Pre-filter** — Shannon entropy + 25+ regex patterns reduce AI token usage
- **Multiple providers** — GitHub, GitLab (self-hosted), Gitea/Forgejo, plain git URLs
- **OpenAI or Ollama** — configurable AI backend with custom URL support
- **SQLite or PostgreSQL** — zero-config default, enterprise-ready option
- **Mattermost alerts** — optional real-time notifications
- **Process lock** — safe for cron; concurrent instances prevented automatically
- **Large repo guard** — configurable size limit with logging and notifications
- **Secure clone** — hooks disabled, exec bits removed, temp dir cleaned up
- **Cross-platform** — Linux, macOS, Windows (Python 3.11+)

## Quick start

```bash
# 1. Clone
git clone https://github.com/your-org/TokenLeak.git
cd TokenLeak

# 2. Install dependencies only (no package build needed)
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Set TOKENLEAK_AI_API_KEY, TOKENLEAK_AI_MODEL, etc.

# 4. Check status
python tokenleak.py status

# 5. Scan a repository
python tokenleak.py scan https://github.com/user/repo.git

# 6. Scan all repos from a list
echo "https://github.com/user/repo1.git" > repos.txt
echo "github:my-org-name" >> repos.txt
python tokenleak.py scan
```

## Installation

**Recommended — no package build, just dependencies:**
```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python tokenleak.py --version
```

**With PostgreSQL support:**
```bash
pip install -r requirements.txt psycopg2-binary
```

## Usage

```
python -m tokenleak scan    [TARGET ...] [--sha SHA] [--report [FILE]]
                                         [--no-prefilter] [--noanimation]
python -m tokenleak rescan  [TARGET ...] [--sha SHA] [--report [FILE]]
python -m tokenleak status
python -m tokenleak mcp                  # start MCP server over stdio
```

### Target formats

| Specifier | Description |
|-----------|-------------|
| `https://github.com/user/repo.git` | Single repository |
| `github:username` | All repos of a GitHub user |
| `gitlab:username` | All repos on configured GitLab |
| `gitlab:https://host:username` | All repos on a specific GitLab host |
| `gitea:username` | All repos on configured Gitea |
| `server:https://gitlab.host` | All repos on a GitLab server |

### Examples

```bash
# Scan specific commit
python -m tokenleak scan https://github.com/user/repo.git --sha abc123

# Rescan (ignore cached results) + write markdown report
python -m tokenleak rescan github:my-org --report report.md

# Disable pre-filter (AI sees everything)
python -m tokenleak scan https://github.com/user/repo.git --no-prefilter

# No animation (for cron/CI)
python -m tokenleak scan --noanimation
```

## Configuration

All settings via environment variables or a `.env` file. Copy `.env.example` to `.env`.

### Key settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENLEAK_AI_PROVIDER` | `openai` | `openai` or `ollama` |
| `TOKENLEAK_AI_API_KEY` | — | API key (OpenAI) |
| `TOKENLEAK_AI_API_URL` | — | Custom base URL |
| `TOKENLEAK_AI_MODEL` | `gpt-4o` | Model name |
| `TOKENLEAK_DB_TYPE` | `sqlite` | `sqlite` or `postgres` |
| `TOKENLEAK_PREFILTER_ENABLED` | `true` | Disable with `false` or `--no-prefilter` |
| `TOKENLEAK_MAX_REPO_SIZE_MB` | `2048` | Skip repos larger than this |
| `TOKENLEAK_REPOS_LIST_PATH` | `repos.txt` | Input target list |
| `TOKENLEAK_MATTERMOST_URL` | — | Mattermost server URL |
| `TOKENLEAK_MATTERMOST_TOKEN` | — | Personal access token |

See `.env.example` for the full list.

### Using Ollama

```bash
TOKENLEAK_AI_PROVIDER=ollama
TOKENLEAK_AI_API_URL=http://localhost:11434/v1
TOKENLEAK_AI_MODEL=llama3.1:70b
```

## Database

**SQLite** (default) — no setup required, ideal for single-host deployment.

**PostgreSQL** — see [docs/postgresql_setup.md](docs/postgresql_setup.md) for
setup instructions including least-privilege role configuration and revocation
of exec-capable functions.

## Pre-filter

The pre-filter screens files locally before sending to the AI:

- **Entropy analysis** — Shannon entropy > 4.5 on token ≥ 20 chars
- **Regex patterns** — AWS keys, GitHub/GitLab tokens, JWTs, private keys, passwords,
  connection strings, Stripe, Twilio, Slack, Google API, and more
- **Suspicious names** — `.env`, `id_rsa`, `*.pem`, `*.key`, `.htpasswd`, etc.

**Disable** pre-filter to send everything to the AI (more thorough, more expensive):
```bash
TOKENLEAK_PREFILTER_ENABLED=false
# or per-run:
python -m tokenleak scan --no-prefilter
```

## Deployment

See [docs/deployment.md](docs/deployment.md) for full production setup with a
dedicated `tokenleak` system user.

See [docs/cron_setup.md](docs/cron_setup.md) for cron and systemd timer configuration.

## Security

- Cloned repositories are treated as potentially hostile (malware assumption)
- Git hooks are wiped immediately after cloning
- All execute bits removed from the working tree
- Clone directory cleaned up after every scan regardless of outcome
- Application runs as a non-root user
- AI agent is instructed never to use or verify found credentials

## Running tests

```bash
pip install ".[dev]"
pytest tests/ -v
```

## Documentation

| Document | Contents |
|----------|---------|
| [docs/architecture.md](docs/architecture.md) | Component diagram and data flow |
| [docs/workflow.md](docs/workflow.md) | Commands, target formats, flags reference |
| [docs/postgresql_setup.md](docs/postgresql_setup.md) | PostgreSQL setup with security hardening |
| [docs/deployment.md](docs/deployment.md) | Production deployment step-by-step |
| [docs/cron_setup.md](docs/cron_setup.md) | Cron and systemd timer configuration |
| [agent.md](agent.md) | AI agent instructions (can be customised) |

## License

MIT
