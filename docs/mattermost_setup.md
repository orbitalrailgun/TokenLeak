# TokenLeak — Mattermost Integration

TokenLeak sends three types of notifications to Mattermost:

| Trigger | When |
|---------|------|
| **Per-alert** | Immediately when the agent calls `save_alert()` — one message per finding |
| **Scan summary** | After each repository scan completes — severity breakdown + top findings |
| **Large repo skipped** | When a repository exceeds `TOKENLEAK_MAX_REPO_SIZE_MB` and is skipped |

All three are optional. If `TOKENLEAK_MATTERMOST_URL` or `TOKENLEAK_MATTERMOST_TOKEN`
is not set, notifications are silently disabled.

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

The Mattermost API identifies channels by their internal ID (26-character alphanumeric),
not by display name. You need this ID for the `TOKENLEAK_MATTERMOST_CHANNEL` variable.

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

The output is the channel ID to use in `.env`.

---

## 4. Configure `.env`

```bash
# Mattermost server URL — no trailing slash, no /api path
TOKENLEAK_MATTERMOST_URL=https://mattermost.example.com

# Bot token or Personal Access Token
TOKENLEAK_MATTERMOST_TOKEN=your-token-here

# Channel ID (26-character string from step 3)
TOKENLEAK_MATTERMOST_CHANNEL=abcdef1234567890abcdef1234
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
