"""Tests for the pre-filter module."""

from pathlib import Path

import pytest

from tokenleak.scanner.prefilter import filter_file, should_send_to_ai, _shannon


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
        # A random-looking base64 string with high entropy
        f.write_text("secret: Th1sIsAVeryR4nd0mStr1ngW1thH1ghEntr0pyXXXXYYYY\n")
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
