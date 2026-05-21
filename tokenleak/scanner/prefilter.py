"""Pre-filter: fast local detection of high-probability secrets before AI analysis.

Two detection mechanisms run in parallel on every file:
  1. Regex patterns — known secret formats (AWS, JWT, private keys, etc.)
  2. Shannon entropy — lines with entropy > threshold on long-enough tokens

When prefilter is DISABLED (config or --no-prefilter), this module returns
every file as a candidate so the AI sees everything.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Entropy ───────────────────────────────────────────────────────────────────

ENTROPY_THRESHOLD = 4.5   # bits per character
ENTROPY_MIN_LEN = 20      # ignore short tokens


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((f / n) * math.log2(f / n) for f in freq.values())


def _high_entropy_tokens(line: str) -> list[str]:
    tokens = re.split(r"[\s\"'=:,;{}()\[\]]+", line)
    return [t for t in tokens if len(t) >= ENTROPY_MIN_LEN and _shannon(t) >= ENTROPY_THRESHOLD]


# ── Regex patterns ─────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    name: str
    regex: re.Pattern

_PATTERNS: list[Pattern] = [
    # Keys & tokens
    Pattern("aws_access_key",       re.compile(r"AKIA[0-9A-Z]{16}")),
    Pattern("aws_secret_key",       re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]")),
    Pattern("github_token",         re.compile(r"gh[pousr]_[a-zA-Z0-9_]{36,}")),
    Pattern("gitlab_token",         re.compile(r"glpat-[a-zA-Z0-9_\-]{20}")),
    Pattern("google_api_key",       re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    Pattern("slack_token",          re.compile(r"xox[bpoas]-[0-9A-Za-z\-]{10,}")),
    Pattern("stripe_live_key",      re.compile(r"sk_live_[0-9a-zA-Z]{24}")),
    Pattern("twilio_account_sid",   re.compile(r"AC[0-9a-fA-F]{32}")),
    Pattern("heroku_api_key",       re.compile(r"[hH]eroku[^\n]{0,20}[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")),
    Pattern("npm_token",            re.compile(r"npm_[a-zA-Z0-9]{36}")),
    Pattern("pypi_token",           re.compile(r"pypi-[a-zA-Z0-9_\-]{50,}")),
    Pattern("telegram_bot_token",   re.compile(r"\d{8,10}:[a-zA-Z0-9_\-]{35}")),
    # JWT
    Pattern("jwt",                  re.compile(r"ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    # Private keys
    Pattern("private_key_header",   re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    Pattern("private_key_pkcs8",    re.compile(r"-----BEGIN ENCRYPTED PRIVATE KEY-----")),
    # Passwords in code
    Pattern("password_assignment",  re.compile(r"""(?i)pass(?:word|wd)?\s*[=:]\s*['"]\S{8,}['"]""")),
    Pattern("secret_assignment",    re.compile(r"""(?i)secret\s*[=:]\s*['"]\S{8,}['"]""")),
    Pattern("api_key_assignment",   re.compile(r"""(?i)api[_\-]?key\s*[=:]\s*['"]\S{8,}['"]""")),
    Pattern("token_assignment",     re.compile(r"""(?i)\btoken\s*[=:]\s*['"]\S{8,}['"]""")),
    # Connection strings
    Pattern("db_connection_string", re.compile(r"(?:postgresql|mysql|mongodb|redis|amqp)://[^\s'\"]{5,}:[^\s'\"]{3,}@")),
    Pattern("smtp_credentials",     re.compile(r"smtp://[^\s]{3,}:[^\s]{3,}@")),
    # PII patterns
    Pattern("credit_card",          re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11})\b")),
    # .env files
    Pattern("dotenv_secret",        re.compile(r"""^[A-Z_]{3,50}(?:KEY|SECRET|TOKEN|PASS|PWD|PASSWORD|AUTH)\s*=\s*\S{8,}""", re.MULTILINE)),
]

# Suspicious file names (checked against path, not content)
_SUSPICIOUS_NAMES = re.compile(
    r"""(?ix)
    (^|/)
    (
        \.env(\.\w+)?         |   # .env, .env.local, .env.prod
        .*password.*          |
        .*secret.*            |
        .*credential.*        |
        .*token.*             |
        id_rsa                |
        id_dsa                |
        id_ecdsa              |
        id_ed25519            |
        \.htpasswd            |
        \.netrc               |
        \.npmrc               |
        \.pypirc              |
        \.aws/credentials     |
        \.ssh/config
    )
    $
""",
)

_SUSPICIOUS_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".jks", ".keystore"}


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class Match:
    pattern_name: str
    line_number: int
    line: str
    matched_value: str


@dataclass
class FileResult:
    path: Path
    is_suspicious_name: bool
    matches: list[Match] = field(default_factory=list)
    high_entropy_lines: list[tuple[int, str]] = field(default_factory=list)

    @property
    def is_candidate(self) -> bool:
        return self.is_suspicious_name or bool(self.matches) or bool(self.high_entropy_lines)


# ── Public API ────────────────────────────────────────────────────────────────

def filter_file(path: Path, content: str) -> FileResult:
    """Check a single file. Always returns a FileResult; caller checks is_candidate."""
    rel = str(path)
    suspicious_name = bool(
        _SUSPICIOUS_NAMES.search(rel)
        or path.suffix.lower() in _SUSPICIOUS_EXTENSIONS
    )
    result = FileResult(path=path, is_suspicious_name=suspicious_name)

    for lineno, line in enumerate(content.splitlines(), start=1):
        for pat in _PATTERNS:
            m = pat.regex.search(line)
            if m:
                result.matches.append(Match(
                    pattern_name=pat.name,
                    line_number=lineno,
                    line=line.strip()[:500],
                    matched_value=m.group(0)[:200],
                ))

        high = _high_entropy_tokens(line)
        if high:
            result.high_entropy_lines.append((lineno, line.strip()[:500]))

    return result


def should_send_to_ai(result: FileResult, enabled: bool) -> bool:
    """Return True if this file should be sent to the AI for deeper analysis."""
    if not enabled:
        return True   # prefilter disabled — send everything
    return result.is_candidate
