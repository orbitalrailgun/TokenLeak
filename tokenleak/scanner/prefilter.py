"""Pre-filter: fast local detection of high-probability secrets before AI analysis.

Three detection mechanisms run on every file:
  1. Exclusion — template/example files are dropped before any analysis
  2. Regex patterns — known secret formats (AWS, JWT, private keys, etc.)
     with placeholder suppression (CHANGE_ME, sk-..., etc. are discarded)
  3. Shannon entropy — lines with entropy > threshold on long-enough tokens

When prefilter is DISABLED (config or --no-prefilter), this module returns
every non-excluded file as a candidate so the AI sees everything.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── Entropy ───────────────────────────────────────────────────────────────────

ENTROPY_THRESHOLD = 4.5
ENTROPY_MIN_LEN = 20


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((f / n) * math.log2(f / n) for f in freq.values())


def _high_entropy_tokens(line: str) -> list[str]:
    tokens = re.split(r"[\s\"'=:,;{}()\[\]]+", line)
    return [t for t in tokens if len(t) >= ENTROPY_MIN_LEN and _shannon(t) >= ENTROPY_THRESHOLD]


# ── Exclusion: template / example files ───────────────────────────────────────
# These files are documentation for configuration — never real secrets.

_EXCLUDED_NAMES = re.compile(
    r"""(?ix)
    (^|/)
    (
        \.env\.(example|sample|template|dist|test|demo|default|placeholder) |
        .*\.(example|sample|template|dist)$  |
        example[_-].*\.(env|cfg|conf|ini|yaml|yml|json|toml) |
        .*[_-](example|sample|template)\.(env|cfg|conf|ini|yaml|yml|json|toml)
    )
    $
""",
)


def is_excluded(path: Path) -> bool:
    """Return True for template/example files that should never be scanned."""
    return bool(_EXCLUDED_NAMES.search(str(path)))


# ── Placeholder suppression ───────────────────────────────────────────────────
# Values that look like secrets but are obviously fake templates.

_PLACEHOLDER_RE = re.compile(
    r"""(?ix)
    # Word-boundary anchored keywords
    \b(
        change[_-]?me                                               |
        your[_-]?(api[_-]?)?(key|token|secret|password)([_-]?here)? |
        enter[_-]?your                                              |
        insert[_-]?(key|token|secret|password)                      |
        example[_-]?(key|token|secret|password)                     |
        dummy[_-]?(key|token|secret|password)                       |
        fake[_-]?(key|token|secret|password)                        |
        test[_-]?(key|token|secret|password)                        |
        placeholder                                                 |
        n/?a                                                        |
        xxx+                                                        |
        todo                                                        |
        fixme
    )\b
    |
    # Prefix match — REPLACE_WITH* covers REPLACE_WITH_STRONG_PASSWORD etc.
    replace[_-]?with
    |
    # Ellipsis patterns: sk-..., ghp_XXXX, glpat-...
    (?:sk|pk|ghp|glpat|xox)[-_][.…]{2,}   |
    [A-Z0-9]{3,}X{4,}                      |   # AKIAXXXXXXXXXXXXXXXX
    <[A-Z_\s]{3,}>                          |   # <YOUR_KEY_HERE>
    \$\{[^}]{3,}\}                          |   # ${VARIABLE_NAME}
    %\([^)]{3,}\)s                              # %(variable_name)s
""",
)


def _is_placeholder_line(line: str) -> bool:
    """Return True if the line's value is clearly a template placeholder."""
    # Only check the value part (right of = or :)
    m = re.search(r"[=:]\s*(.+)$", line)
    value = m.group(1).strip().strip("'\"`") if m else line
    return bool(_PLACEHOLDER_RE.search(value))


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
    # PII
    Pattern("credit_card",          re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11})\b")),
    # .env files (real values, not blank)
    Pattern("dotenv_secret",        re.compile(r"""^[A-Z_]{3,50}(?:KEY|SECRET|TOKEN|PASS|PWD|PASSWORD|AUTH)\s*=\s*\S{8,}""", re.MULTILINE)),
]

# Suspicious file names — real config files, NOT examples/templates
# Note: .env.example is handled by _EXCLUDED_NAMES above, not here.
_SUSPICIOUS_NAMES = re.compile(
    r"""(?ix)
    (^|/)
    (
        \.env$                |   # exactly .env
        \.env\.(local|prod|dev|staging|production|development|ci|test\.local) |
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
    is_excluded_file: bool = False
    matches: list[Match] = field(default_factory=list)
    high_entropy_lines: list[tuple[int, str]] = field(default_factory=list)

    @property
    def is_candidate(self) -> bool:
        if self.is_excluded_file:
            return False
        return self.is_suspicious_name or bool(self.matches) or bool(self.high_entropy_lines)


# ── Public API ────────────────────────────────────────────────────────────────

def filter_file(path: Path, content: str) -> FileResult:
    """Check a single file. Always returns a FileResult; caller checks is_candidate."""
    # Template/example files are always excluded — no AI analysis needed.
    if is_excluded(path):
        return FileResult(path=path, is_suspicious_name=False, is_excluded_file=True)

    rel = str(path)
    suspicious_name = bool(
        _SUSPICIOUS_NAMES.search(rel)
        or path.suffix.lower() in _SUSPICIOUS_EXTENSIONS
    )
    result = FileResult(path=path, is_suspicious_name=suspicious_name)

    for lineno, line in enumerate(content.splitlines(), start=1):
        # Skip lines that are clearly placeholder/template values
        if _is_placeholder_line(line):
            continue

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
    if result.is_excluded_file:
        return False
    if not enabled:
        return True   # prefilter disabled — send everything
    return result.is_candidate
