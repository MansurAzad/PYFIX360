"""Builtin automated fixers.

Each fixer rewrites Python source for one specific finding rule. Fixers must
be conservative: they only touch the reported site, they never reformat the
rest of the file, and every result is validated with ``compile()`` before it
can be applied. Fixers return ``None`` when they cannot fix safely — the
repair plan then records the issue as plan-only (manual guidance).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

from pyfix360.models import Finding

FixerFn = Callable[[str, Finding], "FixOutcome | None"]


@dataclass
class FixOutcome:
    new_source: str
    explanation: str
    risk: str = "low"


def _valid(source: str) -> bool:
    try:
        compile(source, "<fix>", "exec")
        return True
    except SyntaxError:
        return False


def fix_bare_except(source: str, finding: Finding) -> FixOutcome | None:
    """Rewrite ``except:`` into ``except Exception:`` at the reported line."""
    if not finding.line_start:
        return None
    lines = source.splitlines(keepends=True)
    idx = finding.line_start - 1
    if idx >= len(lines):
        return None
    line = lines[idx]
    stripped = line.lstrip()
    if not stripped.startswith("except:"):
        return None
    indent = line[: len(line) - len(stripped)]
    lines[idx] = indent + "except Exception:" + stripped[len("except:"):]
    new_source = "".join(lines)
    if not _valid(new_source):
        return None
    return FixOutcome(
        new_source=new_source,
        explanation="Replaced bare 'except:' with 'except Exception:' so control-flow "
                    "exceptions (KeyboardInterrupt, SystemExit) propagate normally.",
        risk="low",
    )


def fix_yaml_load(source: str, finding: Finding) -> FixOutcome | None:
    """Rewrite ``yaml.load(...)`` into ``yaml.safe_load(...)`` when safe."""
    if "yaml.load(" not in source:
        return None
    # only when no explicit Loader= is passed anywhere (conservative)
    if "Loader=" in source or "Loader =" in source:
        return None
    new_source = source.replace("yaml.load(", "yaml.safe_load(")
    if not _valid(new_source):
        return None
    return FixOutcome(
        new_source=new_source,
        explanation="Switched yaml.load() to yaml.safe_load(), preventing arbitrary "
                    "object construction from untrusted YAML documents.",
        risk="medium",
    )


def _insert_before_closing_paren(text: str, open_idx: int, insertion: str) -> str | None:
    """Insert text just before the ')' matching the '(' at open_idx."""
    depth = 0
    quote: str | None = None
    i = open_idx
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[:i] + insertion + text[i:]
        i += 1
    return None


def fix_weak_hash(source: str, finding: Finding) -> FixOutcome | None:
    """Add usedforsecurity=False to hashlib.md5/sha1 calls (non-crypto intent).

    Only applied when the call has no existing keyword arguments, keeping the
    rewrite minimal and Python-3.9+ compatible.
    """
    title_l = (finding.title or "").lower().replace("-", "")
    algo = "md5" if "md5" in title_l else "sha1"
    needle = f"hashlib.{algo}("
    idx = source.find(needle)
    if idx < 0:
        return None
    open_paren = idx + len(needle) - 1
    call_src = source[open_paren: source.find("\n", open_paren)]
    if "usedforsecurity" in call_src or "=" in call_src.split(")", 1)[0]:
        return None
    new_source = _insert_before_closing_paren(source, open_paren, ", usedforsecurity=False")
    if new_source is None or not _valid(new_source):
        return None
    return FixOutcome(
        new_source=new_source,
        explanation=f"Marked hashlib.{algo}() as usedforsecurity=False to signal "
                    "non-cryptographic use (avoids deprecated weak-hash behavior).",
        risk="low",
    )


FIXERS: dict[str, FixerFn] = {
    "py-bare-except": fix_bare_except,
    "sec-yaml-load": fix_yaml_load,
    "sec-weak-hash": fix_weak_hash,
}


def propose_fix(source: str, finding: Finding) -> FixOutcome | None:
    """Try to fix a finding with a builtin fixer."""
    fixer = FIXERS.get(finding.rule_id)
    if fixer is None:
        return None
    try:
        return fixer(source, finding)
    except Exception:
        return None


def can_auto_fix(rule_id: str) -> bool:
    return rule_id in FIXERS


# ------------------------------------------------------------------------------------
# Repair planning
# ------------------------------------------------------------------------------------

_MANUAL_GUIDANCE = {
    "py-mutable-default": "Replace the mutable default with None and initialize inside "
                          "the function body.",
    "py-swallowed-exception": "Log the exception with context instead of passing silently.",
    "sec-dynamic-exec": "Remove dynamic evaluation; dispatch via explicit logic.",
    "sec-subprocess-shell": "Pass an argument list with shell=False and validate inputs.",
    "sec-os-system": "Replace os.system with subprocess.run([...], shell=False).",
    "sec-pickle": "Switch to a safe serialization format (JSON) for untrusted data.",
    "sec-assert-auth": "Raise a real exception (e.g. PermissionError) instead of assert.",
    "py-syntax-error": "The file does not parse; repair must be manual or AI-assisted.",
}


def build_plan_steps(finding: Finding) -> list[dict]:
    """Produce ordered, explainable repair steps for one finding."""
    if can_auto_fix(finding.rule_id):
        return [
            {"order": 1, "action": "snapshot",
             "detail": "Create a recoverable snapshot before touching the project.",
             "risk": "low", "requires_approval": False},
            {"order": 2, "action": "apply_builtin_fix",
             "detail": f"Apply builtin fixer for {finding.rule_id} in {finding.file_path}.",
             "risk": "low", "requires_approval": False},
            {"order": 3, "action": "validate_syntax",
             "detail": "Compile the patched file to prove it still parses.",
             "risk": "low", "requires_approval": False},
            {"order": 4, "action": "run_tests",
             "detail": "Run the project's test suite in the isolated workspace.",
             "risk": "low", "requires_approval": False},
            {"order": 5, "action": "compare_baseline",
             "detail": "Compare post-repair test results with the pre-repair baseline.",
             "risk": "low", "requires_approval": False},
        ]
    steps = [
        {"order": 1, "action": "manual_guidance",
         "detail": _MANUAL_GUIDANCE.get(finding.rule_id,
                                        "No builtin fixer; review manually or with AI assistance."),
         "risk": "low", "requires_approval": False},
    ]
    if finding.severity.value in ("critical", "high"):
        steps.append({"order": 2, "action": "ai_proposal",
                      "detail": "Optionally request an AI repair proposal (requires an "
                                "enabled provider and your approval).",
                      "risk": "medium", "requires_approval": True})
    return steps


def module_of(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None
