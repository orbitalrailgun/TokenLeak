# TokenLeak — Cron Configuration

## How the lock prevents concurrent runs

TokenLeak writes its PID to `TOKENLEAK_LOCK_FILE` (default: `/tmp/tokenleak.pid`)
at the start of every `scan` or `rescan` command.

If cron fires a new instance while the previous one is still running:
1. The new instance reads the PID file
2. Checks whether that process is alive (`os.kill(pid, 0)`)
3. If alive → exits immediately with an error message logged to syslog
4. If dead (crashed) → removes the stale lock and proceeds normally

This makes it completely safe to schedule short cron intervals — a hung scan
will not accumulate zombie instances.

## Recommended cron configuration

Install as the `tokenleak` user's crontab (never as root):

```bash
sudo -u tokenleak crontab -e
```

Paste the following:

```cron
# TokenLeak scheduled scan — every day at 02:00
# Lock prevents concurrent runs automatically.
SHELL=/bin/bash
TOKENLEAK_ENV=/opt/tokenleak/.env

# m h dom mon dow command
0 2 * * * cd /opt/tokenleak/app && \
  set -a && source /opt/tokenleak/.env && set +a && \
  /opt/tokenleak/venv/bin/python -m tokenleak scan \
    --noanimation \
    >> /opt/tokenleak/logs/cron.log 2>&1
```

### Log rotation

Add a logrotate config so cron.log doesn't grow unbounded:

```
# /etc/logrotate.d/tokenleak
/opt/tokenleak/logs/cron.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
    create 0640 tokenleak tokenleak
}
```

Create the logs directory:
```bash
sudo -u tokenleak mkdir -p /opt/tokenleak/logs
```

## Alternative: systemd timer (recommended over cron)

systemd timers offer better logging integration and dependency management.

### 1. Service unit

```ini
# /etc/systemd/system/tokenleak.service
[Unit]
Description=TokenLeak git repository security scan
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=oneshot
User=tokenleak
Group=tokenleak
WorkingDirectory=/opt/tokenleak/app
EnvironmentFile=/opt/tokenleak/.env
ExecStart=/opt/tokenleak/venv/bin/python -m tokenleak scan --noanimation
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tokenleak
# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/opt/tokenleak
ProtectHome=yes
```

### 2. Timer unit

```ini
# /etc/systemd/system/tokenleak.timer
[Unit]
Description=Run TokenLeak daily at 02:00
Requires=tokenleak.service

[Timer]
OnCalendar=daily
OnCalendar=*-*-* 02:00:00
RandomizedDelaySec=300   # randomise ±5 min to avoid thundering herd
Persistent=true          # catch up if the machine was off

[Install]
WantedBy=timers.target
```

### 3. Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tokenleak.timer
sudo systemctl list-timers tokenleak.timer
```

### 4. Manual trigger

```bash
sudo systemctl start tokenleak.service
journalctl -u tokenleak.service -f
```

## Environment variable loading

The application loads `.env` automatically via `python-dotenv`.
If you prefer to manage env explicitly in cron, use:

```cron
0 2 * * * env $(cat /opt/tokenleak/.env | grep -v '^#' | xargs) \
  /opt/tokenleak/venv/bin/python -m tokenleak scan --noanimation \
  >> /opt/tokenleak/logs/cron.log 2>&1
```

## User-level crontab vs /etc/cron.d

**Preferred**: user-level crontab (`crontab -e` as `tokenleak`).
This ensures jobs run as `tokenleak` without specifying it in each line.

**Alternative**: `/etc/cron.d/tokenleak` with explicit user:

```cron
# /etc/cron.d/tokenleak
SHELL=/bin/bash
0 2 * * * tokenleak cd /opt/tokenleak/app && \
  source /opt/tokenleak/.env && \
  /opt/tokenleak/venv/bin/python -m tokenleak scan --noanimation \
  >> /opt/tokenleak/logs/cron.log 2>&1
```

The file must be `chmod 644` and owned by root for crond to accept it.

## Checking that the lock works

To manually test the lock:

```bash
# Terminal 1 — start a slow scan (will hold lock)
sudo -u tokenleak /opt/tokenleak/venv/bin/python -m tokenleak scan

# Terminal 2 — try to start another instance
sudo -u tokenleak /opt/tokenleak/venv/bin/python -m tokenleak scan
# Expected: ERROR: Another tokenleak instance is already running (PID XXXXX). Lock file: /opt/tokenleak/tokenleak.pid
```
