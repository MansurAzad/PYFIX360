"""External analyzer adapters.

Each adapter knows how to (1) check availability, (2) run against the project
root with a timeout, and (3) translate tool output into pyfix360 findings.
Unavailable tools are reported as skipped — never silently ignored.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from pyfix360.analysis.fingerprints import root_cause_group, stable_key
from pyfix360.models import (
    Confidence,
    Evidence,
    Finding,
    FindingCategory,
    Severity,
)

_SEVERITY_MAP = {
    "critical": Severity.CRITICAL, "high": Severity.HIGH, "medium": Severity.MEDIUM,
    "low": Severity.LOW, "info": Severity.INFO, "note": Severity.INFO,
    "warning": Severity.MEDIUM, "error": Severity.HIGH,
}


@dataclass
class ToolResult:
    tool: str
    ran: bool
    findings: list[Finding] = field(default_factory=list)
    skip_reason: str = ""
    raw_output: str = ""
    exit_code: int | None = None


class ToolAdapter(ABC):
    name: str = "tool"

    @abstractmethod
    def is_available(self, tool_paths: dict[str, str] | None = None) -> tuple[bool, str]:
        """Return (available, reason_if_not)."""

    @abstractmethod
    def run(self, root: Path, *, timeout_s: int = 300,
            tool_paths: dict[str, str] | None = None) -> ToolResult:
        ...

    def _exec(self, args: list[str], *, timeout_s: int, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s,
            encoding="utf-8", errors="replace",
        )


def resolve_tool(name: str, tool_paths: dict[str, str] | None = None) -> str | None:
    """The executable to run for a tool.

    A path configured in Settings → Tools wins over anything on ``PATH``; a
    configured path that does not exist is *not* silently ignored, because a
    typo in the settings would otherwise run a different tool than the one
    the user named.
    """
    if tool_paths:
        candidate = (tool_paths.get(name) or "").strip()
        if candidate:
            return candidate if Path(candidate).exists() else None
    return shutil.which(name)


class RuffAdapter(ToolAdapter):
    name = "ruff"
    _SEV_BY_CODE_PREFIX = {"S": Severity.HIGH, "F": Severity.MEDIUM, "E": Severity.LOW}

    def is_available(self, tool_paths: dict[str, str] | None = None) -> tuple[bool, str]:
        if resolve_tool(self.name, tool_paths):
            return True, ""
        try:
            proc = self._exec(["ruff", "--version"], timeout_s=15, cwd=Path.cwd())
            return proc.returncode == 0, "" if proc.returncode == 0 else "ruff not on PATH"
        except (OSError, subprocess.TimeoutExpired):
            return False, "ruff executable not found on PATH"

    def run(self, root: Path, *, timeout_s: int = 300,
            tool_paths: dict[str, str] | None = None) -> ToolResult:
        exe = resolve_tool(self.name, tool_paths) or "ruff"
        args = [exe, "check", "--output-format", "json", "--exit-zero", "."]
        try:
            proc = self._exec(args, timeout_s=timeout_s, cwd=root)
        except subprocess.TimeoutExpired:
            return ToolResult(self.name, False, skip_reason=f"timed out after {timeout_s}s")
        except OSError as exc:
            return ToolResult(self.name, False, skip_reason=str(exc))

        findings: list[Finding] = []
        try:
            payload = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return ToolResult(self.name, False, skip_reason="could not parse ruff JSON output",
                              raw_output=proc.stdout[:4000], exit_code=proc.returncode)

        for item in payload:
            code = item.get("code") or "ruff"
            rel = str(item.get("filename", "")).replace("\\", "/")
            if str(root).replace("\\", "/") in rel:
                rel = rel.split(str(root).replace("\\", "/"), 1)[1].lstrip("/")
            msg = item.get("message", "")
            location = item.get("location") or {}
            end = item.get("end_location") or {}
            line_start = location.get("row")
            line_end = end.get("row") or line_start
            snippet = (item.get("fix") or {}).get("message") or ""
            sev = self._SEV_BY_CODE_PREFIX.get(code[:1], Severity.LOW)
            if code.startswith("S"):
                category = FindingCategory.security
            elif code.startswith(("F", "E", "W")):
                category = FindingCategory.style
            else:
                category = FindingCategory.logic
            findings.append(Finding(
                stable_key=stable_key(f"ruff:{code}", rel, None, msg),
                rule_id=f"ruff:{code}",
                category=category,
                severity=sev,
                confidence=Confidence.HIGH,
                title=msg or code,
                description=f"Ruff rule {code}: {msg}",
                file_path=rel,
                line_start=line_start,
                line_end=line_end,
                tool="ruff",
                root_cause_group=root_cause_group(f"ruff:{code}", rel, None),
                evidence=[Evidence(kind="source", file_path=rel, line_start=line_start,
                                   line_end=line_end, snippet=snippet or msg,
                                   excerpt=f"Ruff {code}")],
            ))
        return ToolResult(self.name, True, findings=findings, raw_output=proc.stdout[:8000],
                          exit_code=proc.returncode)


class BanditAdapter(ToolAdapter):
    name = "bandit"
    _SEV = {"HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW}

    def is_available(self, tool_paths: dict[str, str] | None = None) -> tuple[bool, str]:
        if resolve_tool(self.name, tool_paths):
            return True, ""
        try:
            proc = self._exec(["bandit", "--version"], timeout_s=15, cwd=Path.cwd())
            return proc.returncode == 0, ""
        except (OSError, subprocess.SubprocessError):
            return False, "bandit not installed (pip install bandit)"

    def run(self, root: Path, *, timeout_s: int = 300,
            tool_paths: dict[str, str] | None = None) -> ToolResult:
        exe = resolve_tool(self.name, tool_paths) or "bandit"
        args = [exe, "-r", ".", "-f", "json", "-q"]
        try:
            proc = self._exec(args, timeout_s=timeout_s, cwd=root)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return ToolResult(self.name, False, skip_reason=str(exc))
        findings: list[Finding] = []
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return ToolResult(self.name, False, skip_reason="could not parse bandit JSON",
                              raw_output=proc.stdout[:4000])
        for item in payload.get("results", []):
            rel = str(item.get("filename", "")).replace("\\", "/")
            test = item.get("test_name", "")
            sev = self._SEV.get(item.get("issue_severity", "LOW"), Severity.LOW)
            line = item.get("line_number")
            findings.append(Finding(
                stable_key=stable_key(f"bandit:{test}", rel, None, item.get("issue_text", "")),
                rule_id=f"bandit:{test}",
                category=FindingCategory.security,
                severity=sev,
                confidence=Confidence.HIGH,
                title=item.get("issue_text", test),
                description=item.get("issue_text", ""),
                file_path=rel,
                line_start=line,
                line_end=line,
                tool="bandit",
                root_cause_group=root_cause_group(f"bandit:{test}", rel, None),
                evidence=[Evidence(kind="source", file_path=rel, line_start=line,
                                   snippet=(item.get("code") or "")[:600],
                                   excerpt=item.get("issue_text", ""))],
            ))
        return ToolResult(self.name, True, findings=findings, raw_output=proc.stdout[:8000])


class PipAuditAdapter(ToolAdapter):
    """Dependency vulnerability scanning via pip-audit."""

    name = "pip-audit"

    def is_available(self, tool_paths: dict[str, str] | None = None) -> tuple[bool, str]:
        if resolve_tool(self.name, tool_paths):
            return True, ""
        try:
            proc = self._exec(["pip-audit", "--version"], timeout_s=15, cwd=Path.cwd())
            return proc.returncode == 0, ""
        except (OSError, subprocess.SubprocessError):
            return False, "pip-audit not installed (pip install pip-audit)"

    def run(self, root: Path, *, timeout_s: int = 300,
            tool_paths: dict[str, str] | None = None) -> ToolResult:
        exe = resolve_tool(self.name, tool_paths) or "pip-audit"
        req = None
        for candidate in ("requirements.txt",):
            if (root / candidate).exists():
                req = candidate
                break
        args = [exe, "--format", "json", "--progress-spinner", "off"]
        if req:
            args += ["-r", req]
        else:
            args += ["--local"]  # best effort: audit the current environment
        try:
            proc = self._exec(args, timeout_s=timeout_s, cwd=root)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return ToolResult(self.name, False, skip_reason=str(exc))
        findings: list[Finding] = []
        try:
            payload = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            payload = []
        for item in payload if isinstance(payload, list) else []:
            name = item.get("name", "?")
            version = item.get("version", "?")
            vulns = item.get("vulns") or []
            for vuln in vulns:
                vid = vuln.get("id", "?")
                fix = vuln.get("fix_versions") or []
                findings.append(Finding(
                    stable_key=stable_key(f"pip-audit:{vid}", name, None, version),
                    rule_id=f"pip-audit:{vid}",
                    category=FindingCategory.dependency,
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    title=f"{name} {version} vulnerable: {vid}",
                    description=(
                        f"{vid} affects {name}=={version}. "
                        + (f"Fix versions: {', '.join(fix)}." if fix else "No published fix version.")
                    ),
                    file_path=req,
                    tool="pip-audit",
                    root_cause_group=root_cause_group(f"pip-audit:{vid}", name, None),
                    evidence=[Evidence(kind="tool_output", file_path=req,
                                       snippet=json.dumps(vuln)[:800],
                                       excerpt=f"pip-audit reported {vid}")],
                ))
        return ToolResult(self.name, True, findings=findings, raw_output=proc.stdout[:8000])


def default_adapters() -> list[ToolAdapter]:
    return [RuffAdapter(), BanditAdapter(), PipAuditAdapter()]
