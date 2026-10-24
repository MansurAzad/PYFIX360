"""Feature inventory and completeness analysis.

Builds a feature matrix from code evidence (definitions, routes), test
evidence (tests referencing the feature), documentation evidence (README /
docs mentions) and completeness markers (TODO/FIXME inside the body).

A feature found in docs but with no implementation is reported as MISSING.
User-supplied acceptance criteria (one per line) are matched by keyword.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.db.models import FeatureCheckRow, FeatureRow, ModuleRow, ProjectFileRow, ProjectRow
from pyfix360.services import audit

_TODO_RE = re.compile(r"#\s*(TODO|FIXME)\b", re.IGNORECASE)

_ROUTE_DECORATORS = ("route", "get", "post", "put", "delete", "patch")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def analyze_completeness(
    session: Session,
    project_id: int,
    acceptance_criteria: list[str] | None = None,
) -> list[FeatureRow]:
    project = session.get(ProjectRow, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    root = Path(project.root_path)

    # wipe previous analysis for idempotency
    old = session.scalars(select(FeatureRow).where(FeatureRow.project_id == project_id)).all()
    for row in old:
        session.delete(row)
    session.flush()

    files = list(session.scalars(
        select(ProjectFileRow).where(ProjectFileRow.project_id == project_id)
    ))
    modules = list(session.scalars(
        select(ModuleRow).where(ModuleRow.project_id == project_id)
    ))

    # index test & doc content once
    test_index: dict[str, list[str]] = {}
    doc_text = ""
    for pf in files:
        content = _read(root / pf.rel_path)
        if pf.is_test and pf.language == "python":
            for name in re.findall(r"\bdef\s+(test_\w+)|\bclass\s+(Test\w+)", content):
                for n in name:
                    if n:
                        test_index.setdefault(n, []).append(pf.rel_path)
        if (pf.language in ("markdown", "rst") and "readme" in pf.rel_path.lower()) or \
                pf.rel_path.lower().startswith("docs/"):
            doc_text += content.lower()

    features: list[FeatureRow] = []
    for mod in modules:
        if mod.kind not in ("function", "class"):
            continue
        name = mod.name
        if name.startswith("_") and not name.startswith("__"):
            continue  # private implementation detail, not a feature
        src = _read(root / mod.rel_path)
        body = _body_excerpt(src, mod.line_start, mod.line_end)

        checks: list[FeatureCheckRow] = []
        checks.append(FeatureCheckRow(check_kind="code", passed=True,
                                      description=f"Defined in {mod.rel_path}:{mod.line_start}"))

        feat_key = name.lower()
        test_hits = [
            p for test_name, paths in test_index.items()
            if feat_key in test_name.lower()
            or test_name.lower().removeprefix("test_") == feat_key
            for p in paths
        ]
        has_test = any(name in _read(root / p.rel_path) for p in files if p.is_test) \
            or bool(test_hits)
        checks.append(FeatureCheckRow(check_kind="test", passed=has_test,
                                      description="Test coverage references this feature"))

        has_docs = name.lower() in doc_text
        checks.append(FeatureCheckRow(check_kind="docs", passed=has_docs,
                                      description="Mentioned in documentation"))

        has_todo = bool(_TODO_RE.search(body))
        if has_todo:
            checks.append(FeatureCheckRow(check_kind="criteria", passed=False,
                                          description="Unresolved TODO/FIXME in body"))

        is_route = any(f"@{d}(" in body or f".{d}(" in body[:200]
                       for d in _ROUTE_DECORATORS)
        if is_route:
            checks.append(FeatureCheckRow(check_kind="code", passed=True,
                                          description="Detected as an HTTP route handler"))

        if has_todo:
            status, confidence = "partial", "high"
        elif has_test:
            status, confidence = "complete", "medium"
        else:
            status, confidence = "partial", "low"

        feat = FeatureRow(
            project_id=project_id, name=name,
            description=f"{mod.kind} in {mod.rel_path}:{mod.line_start}-{mod.line_end}",
            status=status, confidence=confidence,
            evidence_paths=[mod.rel_path],
        )
        session.add(feat)
        session.flush()
        for c in checks:
            c.feature_id = feat.id
            session.add(c)
        features.append(feat)

    # docs mention features with no implementation => missing
    if doc_text:
        for m in re.finditer(r"\b([a-z][a-z0-9_]{3,})\b", doc_text):
            token = m.group(1)
            if token in {"python", "project", "feature", "should", "their", "about"}:
                continue
            if any(f.name == token for f in features):
                continue
            defined = any(mod.name == token for mod in modules)
            if defined:
                continue
            # only surface tokens that look like identifiers AND appear in headings
            if re.search(rf"^#+\s+.*\b{re.escape(token)}\b", doc_text, re.MULTILINE):
                feat = FeatureRow(
                    project_id=project_id, name=token,
                    description="Mentioned in documentation but no implementation found",
                    status="missing", confidence="low", evidence_paths=["docs"],
                )
                session.add(feat)
                session.flush()
                session.add(FeatureCheckRow(
                    feature_id=feat.id, check_kind="docs", passed=True,
                    description="Documented (README/docs heading)",
                ))
                session.add(FeatureCheckRow(
                    feature_id=feat.id, check_kind="code", passed=False,
                    description="No matching definition found in code",
                ))
                features.append(feat)

    # user acceptance criteria
    for crit in acceptance_criteria or []:
        crit_l = crit.strip().lower()
        if not crit_l:
            continue
        keywords = [w for w in re.findall(r"[a-z0-9_]{3,}", crit_l) if w not in
                    {"the", "and", "for", "with", "that", "this", "must", "should", "shall"}]
        hits = [f for f in features if any(k in f.name.lower() for k in keywords)]
        passed = any(f.status == "complete" for f in hits)
        if not hits:
            feat = FeatureRow(
                project_id=project_id, name=crit.strip(),
                description="User acceptance criterion",
                status="missing", confidence="medium", evidence_paths=[],
            )
            session.add(feat)
            session.flush()
            session.add(FeatureCheckRow(
                feature_id=feat.id, check_kind="criteria", passed=False,
                description="No code evidence matched this criterion",
            ))
            features.append(feat)
        else:
            for f in hits:
                session.add(FeatureCheckRow(
                    feature_id=f.id, check_kind="criteria", passed=passed,
                    description=f"Acceptance criterion: {crit.strip()}",
                ))

    audit.record(session, "feature.analyze", project_id=project_id,
                 details=f"features={len(features)} criteria={len(acceptance_criteria or [])}")
    session.flush()
    return features


def _body_excerpt(src: str, start: int | None, end: int | None) -> str:
    if not start:
        return ""
    lines = src.splitlines()
    end = end or start
    return "\n".join(lines[max(0, start - 1): min(len(lines), end)])


def completeness_matrix(session: Session, project_id: int) -> list[dict]:
    rows = session.scalars(
        select(FeatureRow).where(FeatureRow.project_id == project_id).order_by(FeatureRow.name)
    ).all()
    matrix = []
    for f in rows:
        checks = session.scalars(
            select(FeatureCheckRow).where(FeatureCheckRow.feature_id == f.id)
        ).all()
        matrix.append({
            "id": f.id, "name": f.name, "description": f.description,
            "status": f.status, "confidence": f.confidence,
            "checks": [{"kind": c.check_kind, "passed": c.passed,
                        "description": c.description} for c in checks],
        })
    return matrix
