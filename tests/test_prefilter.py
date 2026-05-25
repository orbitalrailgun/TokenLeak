"""Tests for the pre-filter module."""

from pathlib import Path

import pytest

from tokenleak.scanner.prefilter import (
    filter_file, should_send_to_ai, is_excluded, _shannon, _is_placeholder_line,
)


class TestShannonEntropy:
    def test_uniform_string_max_entropy(self):
        # Perfectly uniform — high entropy
        s = "".join(chr(i) for i in range(256))
        assert _shannon(s) > 7.0

    def test_repeated_char_zero_entropy(self):
        assert _shannon("aaaaaaaaaa") == 0.0

    def test_empty_string(self):
        assert _shannon("") == 0.0


class TestFilterFile:
    def test_aws_key_detected(self, tmp_path):
        f = tmp_path / "config.py"
        f.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
        result = filter_file(f, f.read_text())
        assert result.is_candidate
        assert any(m.pattern_name == "aws_access_key" for m in result.matches)

    def test_private_key_header_detected(self, tmp_path):
        f = tmp_path / "key.pem"
        f.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...")
        result = filter_file(f, f.read_text())
        assert result.is_candidate

    def test_jwt_detected(self, tmp_path):
        f = tmp_path / "app.js"
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        f.write_text(f'const token = "{jwt}";')
        result = filter_file(f, f.read_text())
        assert result.is_candidate
        assert any(m.pattern_name == "jwt" for m in result.matches)

    def test_dotenv_password_assignment(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("DB_PASSWORD=supersecretpassword123\n")
        result = filter_file(f, f.read_text())
        assert result.is_candidate
        assert result.is_suspicious_name  # .env is suspicious by name too

    def test_innocent_file_not_candidate(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("def greet():\n    print('hello world')\n")
        result = filter_file(f, f.read_text())
        assert not result.is_candidate

    def test_high_entropy_string_flagged(self, tmp_path):
        f = tmp_path / "config.yml"
        # A random-looking base64 string with high entropy (no placeholder patterns)
        f.write_text("secret: kB7mQv2nXpR9sLw4jH6tYeZd1aUoFcGiN8WqTMPKbhrV\n")
        result = filter_file(f, f.read_text())
        assert result.is_candidate

    def test_suspicious_filename(self, tmp_path):
        f = tmp_path / "id_rsa"
        f.write_text("some content")
        result = filter_file(f, f.read_text())
        assert result.is_suspicious_name

    def test_pem_extension_suspicious(self, tmp_path):
        f = tmp_path / "server.pem"
        f.write_text("cert data")
        result = filter_file(f, f.read_text())
        assert result.is_suspicious_name

    def test_db_connection_string(self, tmp_path):
        f = tmp_path / "settings.py"
        f.write_text('DATABASE_URL = "postgresql://admin:password123@db.example.com/mydb"')
        result = filter_file(f, f.read_text())
        assert result.is_candidate
        assert any(m.pattern_name == "db_connection_string" for m in result.matches)


class TestExclusion:
    def test_env_example_excluded(self, tmp_path):
        f = tmp_path / ".env.example"
        f.write_text("API_KEY=your-api-key-here\nPASSWORD=REPLACE_WITH_STRONG_PASSWORD\n")
        assert is_excluded(f)
        result = filter_file(f, f.read_text())
        assert result.is_excluded_file
        assert not result.is_candidate

    def test_env_sample_excluded(self, tmp_path):
        f = tmp_path / ".env.sample"
        assert is_excluded(f)

    def test_env_template_excluded(self, tmp_path):
        f = tmp_path / ".env.template"
        assert is_excluded(f)

    def test_real_env_not_excluded(self, tmp_path):
        f = tmp_path / ".env"
        assert not is_excluded(f)

    def test_env_local_not_excluded(self, tmp_path):
        f = tmp_path / ".env.local"
        assert not is_excluded(f)

    def test_excluded_not_sent_even_when_prefilter_disabled(self, tmp_path):
        f = tmp_path / ".env.example"
        f.write_text("SECRET=AKIAIOSFODNN7EXAMPLE")
        result = filter_file(f, f.read_text())
        assert not should_send_to_ai(result, enabled=False)


class TestPlaceholderSuppression:
    def test_placeholder_sk_ellipsis(self):
        assert _is_placeholder_line("API_KEY=sk-...")

    def test_placeholder_replace_with(self):
        assert _is_placeholder_line("DB_PASSWORD=REPLACE_WITH_STRONG_PASSWORD")

    def test_placeholder_ghp_ellipsis(self):
        assert _is_placeholder_line("GITHUB_TOKEN=ghp_...")

    def test_placeholder_your_key(self):
        assert _is_placeholder_line("KEY=your-api-key-here")

    def test_placeholder_template_var(self):
        assert _is_placeholder_line("SECRET=${MY_SECRET}")

    def test_placeholder_angle_brackets(self):
        assert _is_placeholder_line("TOKEN=<REPLACE_THIS>")

    def test_real_password_not_placeholder(self):
        assert not _is_placeholder_line('password = "hunter2real!ABC"')

    def test_real_db_url_not_placeholder(self):
        assert not _is_placeholder_line("DB_URL=postgresql://admin:realpass123@db.host/mydb")

    def test_real_aws_key_not_placeholder(self):
        assert not _is_placeholder_line("AWS_KEY=AKIAIOSFODNN7REALKEY12")


class TestShouldSendToAI:
    def test_candidate_always_sent(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("SECRET=abc")
        result = filter_file(f, f.read_text())
        assert should_send_to_ai(result, enabled=True)

    def test_non_candidate_not_sent_when_enabled(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("print('hello')")
        result = filter_file(f, f.read_text())
        assert not should_send_to_ai(result, enabled=True)

    def test_non_candidate_sent_when_disabled(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("print('hello')")
        result = filter_file(f, f.read_text())
        assert should_send_to_ai(result, enabled=False)
