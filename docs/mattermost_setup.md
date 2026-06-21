# TokenLeak — Mattermost Integration

TokenLeak sends four types of notifications to Mattermost:

| Trigger | When | Requires |
|---------|------|----------|
| **Per-alert** | Immediately when the agent calls `save_alert()` — one message per finding | URL + TOKEN |
| **Scan summary** | After each commit scan completes — severity breakdown + top findings | URL + TOKEN |
| **CSV report** | After the entire repository is scanned — CSV file with all alerts attached | URL + TOKEN + **CHANNEL_ID** |
| **Large repo skipped** | When a repository exceeds `TOKENLEAK_MAX_REPO_SIZE_MB` and is skipped | URL + TOKEN |

All notifications are optional. If `TOKENLEAK_MATTERMOST_URL` or `TOKENLEAK_MATTERMOST_TOKEN`
is not set, notifications are silently disabled. The CSV file upload additionally requires
`TOKENLEAK_MATTERMOST_CHANNEL_ID` (see step 3).

---

## 1. Create a Bot Account (recommended)

Bot accounts are preferred over personal tokens because they are independent of any
specific user, do not expire when a user leaves, and can be given minimal permissions.

**In Mattermost (requires System Admin):**

1. Open **System Console → Integrations → Bot Accounts**
2. Enable "Enable Bot Account Creation" if not already on
3. Go to **Integrations → Bot Accounts → Add Bot Account**
4. Fill in:
   - Username: `tokenleak` (or any name)
   - Display Name: `TokenLeak`
   - Role: **Member** (no admin rights needed)
5. Click **Create Bot Account**
6. Copy the **Token** shown on the confirmation screen — it is displayed only once.
   If you miss it, regenerate it from the bot account page.

> **Alternative — Personal Access Token:** if bot accounts are disabled in your
> organization, use a regular user account instead. Go to
> **Account Settings → Security → Personal Access Tokens → Create Token**.
> The token value is used identically.

---

## 2. Create a Channel

Create a dedicated channel for TokenLeak alerts (public or private):

1. Click **+** next to "Channels" in the sidebar → **Create New Channel**
2. Name: `tokenleak-alerts` (or any name)
3. Type: Private is recommended — alerts contain sensitive path and snippet information
4. Add the bot account as a member of this channel:
   - Open the channel → **Members** → **Add Members** → search for `tokenleak`

---

## 3. Get the Channel ID

The Mattermost file upload API requires the internal channel ID (26-character alphanumeric),
not the display name. This is needed for `TOKENLEAK_MATTERMOST_CHANNEL_ID`.

**Via the Mattermost UI:**

1. Open the channel
2. Click the channel name at the top → **View Info** (or the gear icon → **Edit Channel**)
3. The Channel ID is shown at the bottom of the info panel — copy it

**Via the API (if UI option is unavailable):**

```bash
# Replace SERVER, TOKEN, TEAM_NAME, and CHANNEL_NAME
curl -s -H "Authorization: Bearer TOKEN" \
  "https://SERVER/api/v4/teams/name/TEAM_NAME/channels/name/CHANNEL_NAME" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"
```

The output is the 26-character channel ID.

> `TOKENLEAK_MATTERMOST_CHANNEL_ID` is optional — if not set, CSV file uploads are skipped
> and only text notifications are sent. Per-alert and scan-summary messages work without it.

---

## 4. Configure `.env`

```bash
# Mattermost server URL — no trailing slash, no /api path
TOKENLEAK_MATTERMOST_URL=https://mattermost.example.com

# Bot token or Personal Access Token
TOKENLEAK_MATTERMOST_TOKEN=your-token-here

# Channel name for text notifications (per-alert and scan summary)
TOKENLEAK_MATTERMOST_CHANNEL=tokenleak-alerts

# Channel ID (26-character string from step 3) — required for CSV file uploads
# If omitted, file attachments are skipped; text notifications still work.
TOKENLEAK_MATTERMOST_CHANNEL_ID=abcdef1234567890abcdef1234
```

---

## 5. Verify the Connection

Run a quick connectivity test before your first scan:

```bash
python - <<'EOF'
from tokenleak.config import get_config
from tokenleak.notifications.mattermost import Mattermost

mm = Mattermost(get_config())
if not mm.enabled:
    print("ERROR: Mattermost is not configured (check URL and TOKEN)")
else:
    mm.send("TokenLeak test message — integration is working.")
    print("OK: message sent")
EOF
```

If the message appears in the channel, the integration is ready.

---

## 6. What the Notifications Look Like

### Per-alert notification

Sent automatically each time the agent saves a finding. One message per alert.

```
🔴 **CRITICAL [password]** `config/database.yml` line 12
Hardcoded production database password found in Rails credentials file.
```

```
🟠 **HIGH [token]** `scripts/deploy.sh` line 47
AWS access key hardcoded in deployment script.
```

Severity icons:

| Severity | Icon |
|----------|------|
| critical | 🔴 |
| high     | 🟠 |
| medium   | 🟡 |
| low      | 🔵 |

---

### Scan summary

Sent after every repository scan completes (even if no alerts were found).

**With findings:**

```
### TokenLeak scan complete: `https://github.com/org/repo.git`
**3 alert(s) found:**
  - CRITICAL: 1
  - HIGH: 1
  - MEDIUM: 1

- **[CRITICAL]** `config/database.yml` (password): Hardcoded production database password found in Rails credentials file.
- **[HIGH]** `scripts/deploy.sh` (token): AWS access key hardcoded in deployment script.
- **[MEDIUM]** `.env.backup` (secret): Stripe test secret key committed in backup file.
```

**No findings:**

```
### TokenLeak scan complete: `https://github.com/org/repo.git`
No secrets or sensitive data found.
```

If more than 10 alerts are found, the first 10 are listed with a note:
```
  … and 7 more. See DB scan_id=42.
```

---

### CSV alerts report (file attachment)

Sent once after the entire repository scan finishes (all commits processed). Requires
`TOKENLEAK_MATTERMOST_CHANNEL_ID`.

The post message:
```
📊 TokenLeak — CSV-отчёт | `repo` | scan_id=0 | 12 alert(s)
[attached: tokenleak_repo_2026-06-19_scan0.csv]
```

The CSV includes all alerts for the repository with full context:
`alert_id`, `repo_url`, `repo_provider`, `repo_name`, `commit_sha`, `commit_date`,
`commit_message`, `commit_author`, `branch`, `scan_mode`, `scan_status`, `ai_model`,
`input_tokens`, `output_tokens`, `tokens_used`, `scan_error`, `file_path`,
`line_start`, `line_end`, `alert_type`, `severity`, `description`, `code_snippet`,
`how_used`, `confirmation`, `is_false_positive`, `triggered_by`, `alert_created_at`.

The file is UTF-8 with BOM and opens directly in Excel.

---

### Large repo skipped

```
:warning: **TokenLeak**: Repository `https://github.com/org/big-repo.git` skipped —
size 3142 MB exceeds limit 2048 MB.
```

---

### Agent summary (optional)

At the end of each scan the AI agent may send its own summary via the `send_mattermost`
tool. This is a free-form message written by the model — its format varies but typically
lists what was found or confirms a clean scan. It arrives after the per-alert messages
and before the structured scan summary.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No messages at all | URL or token not set | Check that both `TOKENLEAK_MATTERMOST_URL` and `TOKENLEAK_MATTERMOST_TOKEN` are present in `.env` |
| `403 Forbidden` | Bot not in channel, or token lacks permission | Add the bot account as a channel member; verify token is active |
| `400 Bad Request` | Wrong channel ID | Channel ID must be the 26-char internal ID, not the display name |
| `SSL` errors | Self-signed certificate | Set `TOKENLEAK_MATTERMOST_URL` to `http://` if TLS is not configured, or add the CA to the system trust store |
| Per-alert messages arrive but no summary | Mattermost error during summary | Check application log for `send_scan_summary failed` warning |
| Text notifications work but no CSV file | `TOKENLEAK_MATTERMOST_CHANNEL_ID` not set | Add the 26-char channel ID to `.env` as `TOKENLEAK_MATTERMOST_CHANNEL_ID` |
| CSV upload returns `403` | Bot lacks file upload permission or wrong channel | Ensure bot is a member of the channel; verify channel ID is correct |
