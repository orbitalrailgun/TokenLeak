# LEAKED_TOKENS_FOR_TEST

**All credentials in this directory are 100% SYNTHETIC and non-functional.**  
They are deliberately crafted to match the regex and entropy patterns that TokenLeak
detects, so you can run the tool against its own repository and verify that each
detector type works correctly.

Do not use these strings anywhere outside this directory.  
Do not rotate or revoke them — they were never issued.

---

## How to run

```bash
# Scan only this directory (full mode against current HEAD)
tokenleak rescan --sha HEAD <path-to-this-repo>

# Or point at the repo itself and let it scan everything
tokenleak scan <path-to-this-repo>
```

After the scan, compare the discovered alerts against the table below.

---

## Coverage map

| File | Secret type | Detector |
|------|------------|----------|
| `.env` | AWS keys, DB password, API keys | `dotenv_secret`, `aws_access_key`, `aws_secret_key` |
| `config.py` | GitHub token, Stripe key, password | `github_token`, `stripe_live_key`, `password_assignment` |
| `terraform/main.tf` | AWS provider, Heroku key | `aws_access_key`, `heroku_api_key` |
| `ci/deploy.yml` | npm token, PyPI token, Slack token | `npm_token`, `pypi_token`, `slack_token` |
| `keys/id_rsa` | RSA private key | `private_key_header` |
| `keys/jwt_tokens.txt` | JWT, Telegram bot token | `jwt`, `telegram_bot_token` |
| `db/connection_strings.txt` | PostgreSQL, MongoDB, SMTP | `db_connection_string`, `smtp_credentials` |
| `app/settings.py` | GitLab token, Google API key, secret | `gitlab_token`, `google_api_key`, `secret_assignment`, `api_key_assignment` |
| `app/payment.py` | Credit card numbers | `credit_card` |
</content>
</invoke>