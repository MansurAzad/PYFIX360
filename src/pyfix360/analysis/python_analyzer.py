"""Built-in Python static analyzer (stdlib AST only, fully offline).

Detects syntax errors, common runtime hazards, security-sensitive patterns,
logic smells and completeness markers. Every finding carries file/line
evidence, an enclosing symbol and a stable fingerprint. External tools
(ruff/bandit/...) supplement this through adapters.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from pyfix360.analysis.fingerprints import root_cause_group, stable_key
from pyfix360.models import Confidence, Evidence, Finding, FindingCategory, Severity

# Rules for secret detection
_SECRET_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("hardcoded-aws-key", "Hard-coded AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("hardcoded-private-key", "Embedded private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("hardcoded-api-token", "Possible hard-coded API token",
     re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")),
    ("hardcoded-url-creds", "Credentials embedded in URL", re.compile(r"[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{4,}@")),
]

_MAGIC_CALL_NAMES = {"eval", "exec", "compile", "__import__"}

# Call paths considered security sensitive: (dotted suffix, rule, severity, title, advice)
_SENSITIVE_CALLS: list[tuple[str, str, str, str, str]] = [
    ("subprocess.run", "sec-subprocess-shell", "high", "subprocess with shell=True",
     "Avoid shell=True; pass an argument list instead."),
    ("subprocess.call", "sec-subprocess-shell", "high", "subprocess with shell=True",
     "Avoid shell=True; pass an argument list instead."),
    ("subprocess.Popen", "sec-subprocess-shell", "high", "subprocess with shell=True",
     "Avoid shell=True; pass an argument list instead."),
    ("subprocess.check_output", "sec-subprocess-shell", "high", "subprocess with shell=True",
     "Avoid shell=True; pass an argument list instead."),
    ("os.system", "sec-os-system", "high", "os.system invocation",
     "Use subprocess with an argument list and shell=False."),
    ("pickle.load", "sec-pickle", "high", "Unpickling untrusted data",
     "Pickle can execute arbitrary code; use JSON or a safe format."),
    ("pickle.loads", "sec-pickle", "high", "Unpickling untrusted data",
     "Pickle can execute arbitrary code; use JSON or a safe format."),
    ("marshal.load", "sec-marshal", "medium", "Loading marshalled data",
     "Only load marshalled data you produced yourself."),
    ("yaml.load", "sec-yaml-load", "high", "Unsafe yaml.load",
     "Use yaml.safe_load unless you fully control the input."),
    ("tempfile.mktemp", "sec-insecure-temp", "medium", "Insecure temp file creation",
     "Use tempfile.mkstemp or NamedTemporaryFile."),
    ("hashlib.md5", "sec-weak-hash", "low", "Weak hash algorithm (MD5)",
     "Use SHA-256+, or pass usedforsecurity=False if purely non-cryptographic."),
    ("hashlib.sha1", "sec-weak-hash", "low", "Weak hash algorithm (SHA-1)",
     "Use SHA-256+, or pass usedforsecurity=False if purely non-cryptographic."),
    ("random.random", "sec-weak-random", "low", "Non-cryptographic RNG",
     "Use the secrets module for tokens, salts or keys."),
    ("random.randint", "sec-weak-random", "low", "Non-cryptographic RNG",
     "Use the secrets module for tokens, salts or keys."),
    ("random.choice", "sec-weak-random", "low", "Non-cryptographic RNG",
     "Use the secrets module for tokens, salts or keys."),
]

_MUTABLE_DEFAULT_TYPES = (ast.List, ast.Dict, ast.Set)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = [func.attr]
        cur = func.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _snippet(source_lines: list[str], start: int, end: int, limit: int = 6) -> str:
    start = max(1, start)
    end = min(len(source_lines), max(end, start))
    if end - start + 1 > limit:
        end = start + limit - 1
    return "".join(source_lines[start - 1 : end])


@dataclass
class _Ctx:
    rel_path: str
    source_lines: list[str]
    findings: list[Finding] = field(default_factory=list)

    def add(
        self,
        *,
        rule_id: str,
        category: FindingCategory,
        severity: Severity,
        confidence: Confidence,
        title: str,
        description: str,
        line_start: int,
        line_end: int | None = None,
        symbol: str | None = None,
        tool: str = "pyfix360",
        snippet: str | None = None,
        excerpt: str | None = None,
    ) -> None:
        line_end = line_end or line_start
        snip = snippet if snippet is not None else _snippet(self.source_lines, line_start, line_end)
        key = stable_key(rule_id, self.rel_path, symbol, snip)
        self.findings.append(
            Finding(
                stable_key=key,
                rule_id=rule_id,
                category=category,
                severity=severity,
                confidence=confidence,
                title=title,
                description=description,
                file_path=self.rel_path,
                line_start=line_start,
                line_end=line_end,
                symbol=symbol,
                tool=tool,
                root_cause_group=root_cause_group(rule_id, self.rel_path, symbol),
                evidence=[
                    Evidence(
                        kind="source",
                        file_path=self.rel_path,
                        line_start=line_start,
                        line_end=line_end,
                        snippet=snip,
                        excerpt=excerpt or description,
                    )
                ],
            )
        )


class _Visitor(ast.NodeVisitor):
    def __init__(self, ctx: _Ctx) -> None:
        self.ctx = ctx
        self._scopes: list[str] = []

    # --- symbol tracking ---------------------------------------------
    def _enter(self, name: str, node: ast.AST) -> None:
        self._scopes.append(name)
        self.generic_visit(node)
        self._scopes.pop()

    @property
    def symbol(self) -> str | None:
        return ".".join(self._scopes) if self._scopes else None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)
        self._enter(node.name, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function(node)
        self._enter(node.name, node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter(node.name, node)

    # --- checks ------------------------------------------------------
    def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        sym = self.symbol
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            if isinstance(default, _MUTABLE_DEFAULT_TYPES):
                self.ctx.add(
                    rule_id="py-mutable-default",
                    category=FindingCategory.runtime,
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    title="Mutable default argument",
                    description=(
                        "Mutable default arguments are evaluated once at function "
                        "definition and shared between calls, causing state leaks."
                    ),
                    line_start=default.lineno,
                    line_end=default.end_lineno,
                    symbol=f"{sym}.{node.name}" if sym else node.name,
                    excerpt="Default value is a list/dict/set literal.",
                )

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        sym = self.symbol

        if name in _MAGIC_CALL_NAMES or name.endswith(".__import__"):
            self.ctx.add(
                rule_id="sec-dynamic-exec",
                category=FindingCategory.security,
                severity=Severity.HIGH,
                confidence=Confidence.HIGH,
                title=f"Dynamic code execution via {name}()",
                description=(
                    f"{name}() executes arbitrary code at runtime. It is frequently a "
                    "security risk and can hide runtime failures."
                ),
                line_start=node.lineno,
                line_end=node.end_lineno,
                symbol=sym,
                excerpt=f"Call to {name}(...)",
            )

        for suffix, rule, sev, title, advice in _SENSITIVE_CALLS:
            if name == suffix or name.endswith("." + suffix):
                args_text = ast.unparse(node)
                if rule == "sec-subprocess-shell" and "shell=True" not in args_text:
                    continue
                if rule == "sec-weak-random" and "secrets" in name:
                    continue
                self.ctx.add(
                    rule_id=rule,
                    category=FindingCategory.security,
                    severity=Severity(sev),
                    confidence=Confidence.HIGH if rule.startswith("sec-subprocess") else Confidence.MEDIUM,
                    title=title,
                    description=f"{title} at {name}(). {advice}",
                    line_start=node.lineno,
                    line_end=node.end_lineno,
                    symbol=sym,
                    excerpt=args_text[:400],
                )
                break

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        sym = self.symbol
        if node.type is None:
            self.ctx.add(
                rule_id="py-bare-except",
                category=FindingCategory.logic,
                severity=Severity.MEDIUM,
                confidence=Confidence.HIGH,
                title="Bare except clause",
                description=(
                    "Bare 'except:' catches BaseException including KeyboardInterrupt "
                    "and SystemExit, hiding real errors."
                ),
                line_start=node.lineno,
                line_end=node.end_lineno,
                symbol=sym,
                excerpt=_snippet(self.ctx.source_lines, node.lineno, node.lineno),
            )
        elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
            body_stmts = node.body
            swallows = any(isinstance(s, ast.Pass) for s in body_stmts) or all(
                isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) for s in body_stmts
            )
            if swallows:
                self.ctx.add(
                    rule_id="py-swallowed-exception",
                    category=FindingCategory.logic,
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    title="Exception silently swallowed",
                    description=(
                        "'except Exception: pass' hides failures and makes production "
                        "issues very hard to diagnose."
                    ),
                    line_start=node.lineno,
                    line_end=node.end_lineno,
                    symbol=sym,
                    excerpt=_snippet(self.ctx.source_lines, node.lineno, node.end_lineno or node.lineno),
                )
        self.generic_visit(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:  # pragma: no cover - 3.11+
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        # asserts disappear under -O; bad for security/validation gates in app code
        if self._scopes and "test" in self._scopes[0].lower():
            return
        msg = ast.unparse(node.msg) if node.msg else ""
        if any(w in msg.lower() for w in ("permission", "auth", "admin", "role", "token")):
            self.ctx.add(
                rule_id="sec-assert-auth",
                category=FindingCategory.security,
                severity=Severity.HIGH,
                confidence=Confidence.MEDIUM,
                title="Authorization check via assert",
                description=(
                    "assert statements are removed when Python runs with -O; never use "
                    "them for authorization or other security gates."
                ),
                line_start=node.lineno,
                line_end=node.end_lineno,
                symbol=self.symbol,
                excerpt=_snippet(self.ctx.source_lines, node.lineno, node.end_lineno or node.lineno),
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_secret_assignment(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._check_secret_assignment([node.target], node.value)
        self.generic_visit(node)

    _SECRET_NAME = re.compile(
        r"(?i)^(api[_-]?key|api[_-]?secret|secret[_-]?key|secret|access[_-]?token"
        r"|auth[_-]?token|token|password|passwd|pwd|private[_-]?key)$"
    )

    def _check_secret_assignment(self, targets: list[ast.expr], value: ast.expr) -> None:
        """Flag literal secrets assigned to credential-looking names."""
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return
        if len(value.value) < 8:
            return
        for target in targets:
            name = target.id if isinstance(target, ast.Name) else ""
            if name and self._SECRET_NAME.match(name):
                self.ctx.add(
                    rule_id="hardcoded-secret",
                    category=FindingCategory.security,
                    severity=Severity.CRITICAL,
                    confidence=Confidence.MEDIUM,
                    title="Possible hard-coded secret",
                    description=(
                        "A credential-looking name is assigned a literal string. "
                        "Move secrets to environment variables or a credential store."
                    ),
                    line_start=value.lineno,
                    line_end=value.end_lineno or value.lineno,
                    symbol=self.symbol,
                    snippet="[redacted literal assignment]",
                    excerpt=f"assignment to '{name}' with literal value",
                )
                return

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            for rule, title, pattern in _SECRET_PATTERNS:
                if pattern.search(node.value):
                    self.ctx.add(
                        rule_id=rule,
                        category=FindingCategory.security,
                        severity=Severity.CRITICAL,
                        confidence=Confidence.MEDIUM,
                        title=title,
                        description=(
                            "A literal in the source matches a known secret pattern. "
                            "Move secrets to environment variables or a credential store."
                        ),
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        symbol=self.symbol,
                        snippet="[redacted matched literal]",
                        excerpt="Matched pattern: " + rule,
                    )
                    break
        self.generic_visit(node)


_TODO_RE = re.compile(r"#\s*(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE)


def analyze_python_source(rel_path: str, source: str) -> tuple[list[Finding], list[dict]]:
    """Analyze one Python source string.

    Returns (findings, symbols) where symbols is an inventory of
    module/class/function definitions.
    """
    lines = source.splitlines(keepends=True)
    ctx = _Ctx(rel_path=rel_path, source_lines=lines)
    symbols: list[dict] = []

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        ctx.add(
            rule_id="py-syntax-error",
            category=FindingCategory.SYNTAX,
            severity=Severity.CRITICAL,
            confidence=Confidence.HIGH,
            title="Syntax error",
            description=f"{exc.msg}",
            line_start=exc.lineno or 1,
            line_end=(exc.lineno or 1) + 1,
            symbol=None,
            snippet=(lines[exc.lineno - 1].rstrip() if exc.lineno and exc.lineno <= len(lines) else None),
            excerpt=f"Python parser reported: {exc.msg}",
        )
        return ctx.findings, symbols

    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            symbols.append({"kind": "module", "name": rel_path, "line_start": 1,
                            "line_end": len(lines)})
        elif isinstance(node, ast.ClassDef):
            symbols.append({"kind": "class", "name": node.name,
                            "line_start": node.lineno, "line_end": node.end_lineno})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({"kind": "function", "name": node.name,
                            "line_start": node.lineno, "line_end": node.end_lineno})

    _Visitor(ctx).visit(tree)

    # completeness markers
    for idx, raw in enumerate(lines, start=1):
        m = _TODO_RE.search(raw)
        if m:
            ctx.add(
                rule_id="cmp-todo-marker",
                category=FindingCategory.completeness,
                severity=Severity.LOW if m.group(1).upper() == "TODO" else Severity.MEDIUM,
                confidence=Confidence.HIGH,
                title=f"{m.group(1).upper()} marker in source",
                description=(
                    f"Unresolved {m.group(1).upper()} marker: "
                    f"{(m.group(2) or '').strip() or '(no description)'}"
                ),
                line_start=idx,
                line_end=idx,
                symbol=None,
                excerpt=raw.strip()[:200],
            )

    return ctx.findings, symbols


def analyze_python_file(root: Path, rel_path: str) -> tuple[list[Finding], list[dict]]:
    path = root / rel_path
    try:
        # utf-8-sig transparently strips a leading UTF-8 BOM, which Windows
        # editors add routinely. Without it a BOM makes ast.parse raise
        # SyntaxError and we would report a false `py-syntax-error`.
        source = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        f = Finding(
            stable_key=stable_key("io-unreadable", rel_path, None, rel_path),
            rule_id="io-unreadable",
            category=FindingCategory.runtime,
            severity=Severity.MEDIUM,
            confidence=Confidence.HIGH,
            title="File could not be read",
            description=str(exc),
            file_path=rel_path,
        )
        return [f], []
    return analyze_python_source(rel_path, source)
