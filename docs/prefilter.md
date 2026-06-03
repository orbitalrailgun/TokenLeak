# TokenLeak — Pre-filter

The pre-filter runs locally on every file before any AI call. Its job is to quickly
discard safe content so the AI only processes files that are actually likely to contain
secrets. This reduces AI token usage without sacrificing recall.

---

## Why it exists

Scanning a large repository commit-by-commit means potentially sending thousands of
files to the AI. Most files contain no secrets whatsoever — source code, documentation,
generated assets. Sending all of them is expensive and slow. The pre-filter identifies
files with a high probability of containing real secrets using fast local heuristics,
and forwards only those to the AI.

---

## How a file passes or fails the pre-filter

A file is a **candidate** (sent to AI) if ANY of the following is true:

1. Its name matches a **suspicious filename pattern** (e.g. `.env`, `id_rsa`, `*.pem`)
2. Any of its lines matches a **regex pattern** for a known secret format
   (and the line is not a placeholder)
3. Any of its lines contains a **high-entropy token**

A file is **excluded** (never sent to AI, regardless of content) if its name matches
the template/example exclusion list (e.g. `.env.example`, `config.sample.yml`).

---

## Stage 1 — Exclusion

Template and example files are discarded first, before any content analysis.
These files exist to document configuration format — they never contain real secrets.

Files are excluded if their name matches any of these patterns:

| Pattern | Examples |
|---------|---------|
| `.env.<suffix>` where suffix is: `example`, `sample`, `template`, `dist`, `test`, `demo`, `default`, `placeholder` | `.env.example`, `.env.sample`, `.env.template` |
| Any file ending in `.example`, `.sample`, `.template`, `.dist` | `config.sample`, `database.template` |
| `example-*.{env,cfg,conf,ini,yaml,yml,json,toml}` | `example-config.yml` |
| `*-example.{env,...}`, `*_example.{env,...}` | `app-example.env` |

Excluded files are never analysed further — even if they contain what looks like
a real credential. This avoids false positives on documentation.

---

## Stage 2 — Suspicious file names

Certain file names are inherently high-risk regardless of content. A file that matches
any of the following is always forwarded to the AI:

**By name pattern:**

| Pattern | Matches |
|---------|---------|
| `.env` | exactly `.env` |
| `.env.<suffix>` | `.env.local`, `.env.prod`, `.env.dev`, `.env.staging`, `.env.production`, `.env.development`, `.env.ci`, `.env.test.local` |
| `*password*` | any file containing "password" in the name |
| `*secret*` | any file containing "secret" |
| `*credential*` | any file containing "credential" |
| `*token*` | any file containing "token" |
| `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` | SSH private key files |
| `.htpasswd` | Apache password file |
| `.netrc` | FTP/curl credentials |
| `.npmrc` | npm registry credentials |
| `.pypirc` | PyPI credentials |
| `.aws/credentials` | AWS credentials file |
| `.ssh/config` | SSH client config |

**By file extension:**

`.pem`, `.key`, `.p12`, `.pfx`, `.crt`, `.cer`, `.jks`, `.keystore`

---

## Stage 3 — Regex patterns

Each line of the file is tested against 25 regex patterns covering known secret formats.
Lines that are clearly placeholder values are skipped before pattern matching (see
[Placeholder suppression](#placeholder-suppression) below).

| Pattern | What it matches | Example |
|---------|----------------|---------|
| `aws_access_key` | AWS Access Key ID | `AKIAIOSFODNN7EXAMPLE` |
| `aws_secret_key` | AWS Secret Access Key | `aws_secret = "wJalrXUtnFEMI/K7..."` |
| `github_token` | GitHub PAT or Actions token | `ghp_16C7e42F292c6912E7710c838347Ae178B4a` |
| `gitlab_token` | GitLab Personal Access Token | `glpat-xxxxxxxxxxxxxxxxxxxx` |
| `google_api_key` | Google Cloud API key | `AIzaSyD-9tSrke72...` |
| `slack_token` | Slack bot/user/app token | `xoxb-111-222-xxxxxxxxx` |
| `stripe_live_key` | Stripe live secret key | `sk_live_aBcD1234...` |
| `twilio_account_sid` | Twilio Account SID | `ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `heroku_api_key` | Heroku API key | `heroku_key: "12345678-..."` |
| `npm_token` | npm automation token | `npm_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `pypi_token` | PyPI API token | `pypi-AgEIcHlwaS5vcmcAAAA...` |
| `telegram_bot_token` | Telegram bot token | `123456789:AAxxxxxxxxxxxxxxxxxxxxx` |
| `jwt` | JSON Web Token | `eyJhbGciOiJIUzI1NiJ9.eyJzdWIi...` |
| `private_key_header` | PEM private key header | `-----BEGIN RSA PRIVATE KEY-----` |
| `private_key_pkcs8` | Encrypted PKCS8 key | `-----BEGIN ENCRYPTED PRIVATE KEY-----` |
| `password_assignment` | Password hardcoded in code | `password = "hunter2"` |
| `secret_assignment` | Secret hardcoded in code | `secret: "abc123xyz"` |
| `api_key_assignment` | API key hardcoded in code | `api_key = "sk-abc..."` |
| `token_assignment` | Token hardcoded in code | `token = "ghp_abc..."` |
| `db_connection_string` | Database URL with credentials | `postgresql://user:pass@host/db` |
| `smtp_credentials` | SMTP URL with credentials | `smtp://user:pass@mail.example.com` |
| `credit_card` | Credit card number (Luhn-like) | `4111111111111111` |
| `dotenv_secret` | `.env`-style variable with credential suffix | `DATABASE_PASSWORD=prod_secret` |

---

## Placeholder suppression

Before running regex patterns on a line, the pre-filter checks whether the line's
value is clearly a template placeholder. If it is, the line is skipped — even if
the regex would otherwise match.

The following values are recognised as placeholders:

| Category | Examples |
|----------|---------|
| Keyword phrases | `CHANGE_ME`, `your-api-key-here`, `your-token`, `enter-your`, `insert-key`, `example-secret`, `dummy-password`, `fake-token`, `test-key`, `placeholder`, `N/A`, `XXX…`, `TODO`, `FIXME` |
| `replace-with` prefix | `REPLACE_WITH_STRONG_PASSWORD`, `replace-with-token` |
| Ellipsis shorthand | `sk-...`, `ghp_XXXX`, `glpat-...` |
| All-caps with trailing X | `AKIAXXXXXXXXXXXXXXXX` |
| Angle bracket templates | `<YOUR_KEY>`, `<TOKEN_HERE>` |
| Shell variable syntax | `${VARIABLE_NAME}` |
| Python format syntax | `%(variable_name)s` |

Suppression applies only to the value side of an assignment (`key = <value>`).
If the line has no `=` or `:`, the full line is checked.

---

## Stage 4 — Shannon entropy

After regex matching, each line is split into tokens on whitespace and common
delimiters (`"`, `'`, `=`, `:`, `,`, `;`, `{`, `}`, `(`, `)`, `[`, `]`).

A token triggers the entropy check if:
- length ≥ **20 characters**, AND
- Shannon entropy ≥ **4.5 bits/char**

Shannon entropy measures character distribution uniformity. Random-looking strings
(API keys, secrets, hashes) have high entropy; human-readable words have low entropy.

For reference:

| String | Entropy | Classification |
|--------|---------|---------------|
| `password123` | ~3.2 | Low — dictionary word + digits |
| `aaaaaaaaaaaaaaaaaaa` | 0.0 | Low — repetition |
| `wJalrXUtnFEMI/K7MDENG` | ~4.8 | **High** — likely a secret |
| `AKIAIOSFODNN7EXAMPLE` | ~4.1 | Below threshold (caught by regex instead) |
| `eyJhbGciOiJIUzI1NiJ9` | ~4.6 | **High** — JWT segment |

The 4.5 threshold and 20-character minimum are calibrated to catch real secrets while
keeping false positives low on common identifiers like UUIDs and hashes in documentation.
Placeholder suppression does **not** apply to the entropy check — only to regex matching.

---

## Decision summary

```
file received
    │
    ▼
is_excluded(path)?  ──YES──► DROP (never send to AI)
    │
    NO
    ▼
suspicious name or extension?  ──YES──► CANDIDATE ──► send to AI
    │
    NO
    ▼
for each line (skip if placeholder):
    regex match?  ──YES──► CANDIDATE ──► send to AI
    │
    NO
    ▼
for each token in line:
    entropy ≥ 4.5 and len ≥ 20?  ──YES──► CANDIDATE ──► send to AI
    │
    NO
    ▼
no signals found ──► DROP (do not send to AI)
```

---

## Disabling the pre-filter

When the pre-filter is disabled, every non-excluded file is forwarded to the AI.
This is more thorough but significantly more expensive.

```bash
# Permanently in .env
TOKENLEAK_PREFILTER_ENABLED=false

# For a single run
python -m tokenleak scan --no-prefilter https://github.com/org/repo.git
```

Even with the pre-filter disabled, the exclusion list (`.env.example`, etc.) still
applies — those files are never sent to the AI regardless.

---

## Pre-filter in diff mode

In diff mode (per-commit scan), the pre-filter operates on the **added lines** of the
diff rather than the full file. A synthetic file is constructed from the added lines and
passed through the same pipeline. This means:

- Only the lines that actually changed are checked
- The file as a whole is not re-scanned if the changed lines are clean
- Token usage stays proportional to what actually changed, not the file size

---

## Token savings

On a typical Terraform provider repository (e.g. `cloud-ru/evo-terraform`, ~170 commits):

| Phase | Files/commits checked | Passed to AI | Ratio |
|-------|-----------------------|--------------|-------|
| Full scan (HEAD) | all files | only candidates | ~1–5% of files |
| Diff scan (history) | 168 commits | ~15% | most diffs contain only docs/Markdown |

Without the pre-filter, every diff would be sent to the AI regardless of content.
With it, roughly 85% of diff-scan commits are handled locally in under 1 ms with
zero tokens spent.
