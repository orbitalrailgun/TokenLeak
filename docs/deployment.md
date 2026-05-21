# TokenLeak — Deployment Guide

This guide covers a production deployment on Linux using a dedicated non-root user.

## Prerequisites

- Python 3.11+
- Git
- (Optional) PostgreSQL 14+ for persistent, multi-host storage
- (Optional) Access to Mattermost for notifications

## 1. Create a dedicated system user

The `tokenleak` user must not be root. It will own the application and all cloned
repository data. Restricting its shell prevents interactive login.

```bash
sudo useradd --system \
             --shell /sbin/nologin \
             --home-dir /opt/tokenleak \
             --create-home \
             tokenleak
```

## 2. Clone the application repository

```bash
sudo -u tokenleak git clone https://github.com/your-org/TokenLeak.git \
     /opt/tokenleak/app

# Lock down directory permissions
sudo chmod 750 /opt/tokenleak
sudo chmod 750 /opt/tokenleak/app
```

## 3. Create a Python virtual environment

```bash
sudo -u tokenleak python3.11 -m venv /opt/tokenleak/venv

# Install dependencies
sudo -u tokenleak /opt/tokenleak/venv/bin/pip install \
     -r /opt/tokenleak/app/pyproject.toml
# Or: pip install /opt/tokenleak/app
# For PostgreSQL support add: pip install /opt/tokenleak/app[postgres]
```

## 4. Create and configure the .env file

```bash
sudo -u tokenleak cp /opt/tokenleak/app/.env.example /opt/tokenleak/.env
sudo chmod 600 /opt/tokenleak/.env
sudo -u tokenleak nano /opt/tokenleak/.env
```

Minimum required settings:
```bash
TOKENLEAK_AI_API_KEY=sk-...
TOKENLEAK_AI_MODEL=gpt-4o
TOKENLEAK_DB_PATH=/opt/tokenleak/tokenleak.db   # SQLite
TOKENLEAK_CLONE_DIR=/opt/tokenleak/clones
TOKENLEAK_LOCK_FILE=/opt/tokenleak/tokenleak.pid
TOKENLEAK_REPOS_LIST_PATH=/opt/tokenleak/repos.txt
```

## 5. Create the repos list

```bash
cat > /opt/tokenleak/repos.txt << 'EOF'
# One target per line. Comments start with #.
# Plain URLs:
https://github.com/your-org/repo1.git
https://github.com/your-org/repo2.git
# All repos of a GitHub user:
github:some-username
# All repos on a GitLab server:
server:https://gitlab.internal.example.com
EOF
sudo chown tokenleak:tokenleak /opt/tokenleak/repos.txt
sudo chmod 640 /opt/tokenleak/repos.txt
```

## 6. Create required directories

```bash
sudo -u tokenleak mkdir -p /opt/tokenleak/clones
sudo -u tokenleak mkdir -p /opt/tokenleak/reports
sudo chmod 700 /opt/tokenleak/clones   # only tokenleak user can access clones
```

## 7. Verify the installation

```bash
cd /opt/tokenleak/app
sudo -u tokenleak /opt/tokenleak/venv/bin/python -m tokenleak status
```

Expected output: a summary table with zero repos (DB is empty on first run).

## 8. Run a test scan

```bash
# Scan a single public repo to confirm everything works
sudo -u tokenleak \
  TOKENLEAK_REPOS_LIST_PATH=/dev/null \
  /opt/tokenleak/venv/bin/python -m tokenleak \
  scan https://github.com/example/public-repo.git --noanimation
```

## 9. Configure cron (see cron_setup.md)

See [cron_setup.md](cron_setup.md) for the recommended cron configuration.

## 10. Upgrading

```bash
sudo -u tokenleak git -C /opt/tokenleak/app pull
sudo -u tokenleak /opt/tokenleak/venv/bin/pip install /opt/tokenleak/app
# No manual DB migration needed — the app applies schema on startup
```

## 11. Log access

Application logs go to syslog (daemon facility) and stderr.
To view recent logs:

```bash
# systemd journal
journalctl -t tokenleak -n 100

# Or syslog
grep tokenleak /var/log/syslog | tail -100
```

## 12. Security notes

- The `tokenleak` user has no shell (`/sbin/nologin`) — it cannot be used interactively
- The `.env` file is `chmod 600` — only readable by the `tokenleak` user
- The `clones/` directory is `chmod 700` — not accessible to other users
- Clones are deleted after each scan (try/finally in `scanner/clone.py`)
- The PID lock file prevents concurrent instances from the same cron job
