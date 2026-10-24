"""Tests for secret redaction."""

from pyfix360.ai.redaction import redact, redact_diff


def test_redacts_aws_key():
    r = redact('key = "AKIAIOSFODNN7EXAMPLE"')
    assert "AKIA" not in r.text
    assert r.count >= 1


def test_redacts_private_key_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nabc123\n-----END RSA PRIVATE KEY-----"
    r = redact(text)
    assert "abc123" not in r.text
    assert r.count == 1


def test_redacts_password_assignment():
    r = redact('db = connect(password="hunter2hunter2")')
    assert "hunter2hunter2" not in r.text


def test_redacts_bearer_token():
    r = redact("Authorization: Bearer eyJhbGciOi.payload.sig")
    assert "eyJhbGciOi" not in r.text


def test_redacts_github_token():
    r = redact("token=ghp_abcdefghijklmnopqrstuv")
    assert "ghp_" not in r.text


def test_redacts_url_credentials():
    r = redact("db connect to postgres://user:secretpw@host/db")
    assert "secretpw" not in r.text


def test_clean_text_untouched():
    src = "def add(a, b):\n    return a + b\n"
    r = redact(src)
    assert r.text == src
    assert r.count == 0


def test_redact_diff_only_added_lines():
    diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-password = 'hunter2hunter2'\n+password = get_secret()\n"
    r = redact_diff(diff)
    assert "-password = 'hunter2hunter2'" in r.text  # removed line preserved for review
    # added lines are conservatively scrubbed: assignment-shaped values vanish
    assert "get_secret" not in r.text
    assert r.count >= 1
