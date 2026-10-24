"""Secret redaction applied before any content leaves the machine.

Redaction is conservative: anything that looks like a credential is replaced
with a placeholder. The result count is recorded in AI request metadata so
users can see exactly how much sensitive material was stripped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "[REDACTED:private-key]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED:github-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED:api-key]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:slack-token]"),
    (re.compile(r"\bjwt\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.]{20,}"), "[REDACTED:jwt]"),
    (re.compile(r"(?i)\b(authorization|proxy-authorization)\b\s*[:=]\s*.+"),
     "authorization: [REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_\-\._~\+]{10,}"), "Bearer [REDACTED]"),
    (re.compile(
        r"""(?ix)(\b(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token"""
        r"""|auth[_-]?token|passwd|password|pwd|dsn|database_url)\b"""
        r"""\s*[:=]\s*)(["']?)[^"'\s,;\)]{4,}\2"""),
     r"\1\2[REDACTED]\2"),
    (re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:[^\s@/]{4,}@"),
     lambda m: m.group(0).split("://")[0] + "://[REDACTED]@" + m.group(0).split("@", 1)[1]),
]


@dataclass
class RedactionResult:
    text: str
    count: int


def redact(text: str) -> RedactionResult:
    count = 0
    for pattern, repl in _PATTERNS:
        text, n = pattern.subn(repl, text)
        count += n
    return RedactionResult(text=text, count=count)


def redact_diff(diff: str) -> RedactionResult:
    """Redact added lines of a diff (removed lines are the problem being fixed)."""
    out_lines = []
    count = 0
    for line in diff.splitlines(keepends=True):
        if line.startswith("+") and not line.startswith("+++"):
            r = redact(line)
            count += r.count
            out_lines.append(r.text)
        else:
            out_lines.append(line)
    return RedactionResult(text="".join(out_lines), count=count)
