"""Stable identity for findings.

A finding's identifier must survive re-scans and line-number drift. We hash
(rule id, relative path, nearest enclosing symbol, normalized snippet) which is
stable under edits elsewhere in the file while remaining distinct per issue
site. Repeated detections collapse onto the same key and are deduplicated.
"""

from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")


def normalize_snippet(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def stable_key(rule_id: str, rel_path: str, symbol: str | None, snippet: str) -> str:
    payload = "\x1f".join(
        [rule_id, rel_path.replace("\\", "/"), symbol or "<module>", normalize_snippet(snippet)]
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def root_cause_group(rule_id: str, rel_path: str, symbol: str | None) -> str:
    """Issues sharing (rule, file, symbol) are likely one root cause."""
    payload = "\x1f".join([rule_id, rel_path.replace("\\", "/"), symbol or "<module>"])
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]
