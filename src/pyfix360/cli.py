"""PYFIX 360 command-line interface.

Headless access to the full workflow:
    pyfix360 init
    pyfix360 import ./myproject
    pyfix360 scan 1
    pyfix360 findings 1
    pyfix360 plan 3
    pyfix360 repair 1
    pyfix360 report 1 --format markdown
    pyfix360 rollback 1

and to the background host that keeps working after the window is closed:
    pyfix360 service probe
    pyfix360 service install --mode task
    pyfix360 service enqueue 1 --confirmed
    pyfix360 service status
    pyfix360 service approve 4

and to the updater that keeps an installed copy current:
    pyfix360 update --check
    pyfix360 update --yes
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from sqlalchemy import select

from pyfix360 import APP_NAME, __version__
from pyfix360.config import get_config
from pyfix360.credentials.store import store_info
from pyfix360.db import session_scope
from pyfix360.db.models import (
    AuditEventRow,
    FindingRow,
    ProjectRow,
    RepairPlanRow,
    ScanRow,
)
from pyfix360.models import AIProviderConfig, PrivacyMode, ProjectOrigin, ProviderType
from pyfix360.services import (
    ai_provider_service,
    feature_service,
    project_service,
    repair_service,
    report_service,
    scan_service,
    settings_service,
)


def _table(rows: list[list[str]], headers: list[str]) -> str:
    if not rows:
        return "(none)"
    widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    out = ["  ".join(str(h).ljust(w) for h, w in zip(headers, widths, strict=True))]
    out.append("  ".join("-" * w for w in widths))
    for r in rows:
        out.append("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)))
    return "\n".join(out)


# ------------------------------------------------------------------------------------
# commands
# ------------------------------------------------------------------------------------


def cmd_init(args) -> int:
    from pyfix360.db import init_db

    init_db()
    cfg = get_config()
    print(f"{APP_NAME} {__version__}")
    print(f"Data directory : {cfg.data_dir}")
    print(f"Database       : {cfg.db_path}")
    print("Initialized.")
    return 0


def cmd_import(args) -> int:
    from pyfix360.db import init_db

    init_db()
    with session_scope() as s:
        origin = ProjectOrigin(args.origin) if args.origin else None
        project = project_service.import_project(s, args.source, origin=origin, name=args.name)
        print(f"Imported project #{project.id}: {project.name}")
        print(f"  root    : {project.root_path}")
        print(f"  origin  : {project.origin}")
        print(f"  files   : {project.file_count}")
        facts = project.facts or {}
        for key in ("languages", "frameworks", "build_systems", "entry_points",
                    "package_managers", "test_harnesses"):
            vals = facts.get(key) or []
            print(f"  {key:<17}: {', '.join(vals) if vals else '-'}")
    return 0


def cmd_projects(args) -> int:
    with session_scope() as s:
        rows = s.scalars(select(ProjectRow).order_by(ProjectRow.id)).all()
        print(_table(
            [[p.id, p.name, p.origin, p.file_count, p.last_scan_id or "-",
              p.root_path] for p in rows],
            ["id", "name", "origin", "files", "last_scan", "root"],
        ))
    return 0


def cmd_scan(args) -> int:
    with session_scope() as s:
        tools = args.tools.split(",") if args.tools else None
        scan = scan_service.create_scan(s, args.project_id, tools)

        def progress(pct: float, msg: str) -> None:
            print(f"  [{pct:5.0%}] {msg}")

        scan = scan_service.run_scan(s, scan.id, incremental=not args.full,
                                     timeout_s=get_config().execution_timeout_s,
                                     progress=progress)
        print(f"Scan #{scan.id} finished: {scan.status}")
        print(f"  findings   : {scan.findings_count}")
        print(f"  scanned    : {scan.files_scanned} (skipped {scan.files_skipped})")
        print(f"  tools ran  : {', '.join(scan.tools_ran) or '-'}")
        if scan.tools_skipped:
            print("  NOT available (results incomplete):")
            for t in scan.tools_skipped:
                print(f"    - {t}")
        if scan.message:
            print(f"  message    : {scan.message}")
    return 0


def cmd_findings(args) -> int:
    from pyfix360.services import findings_service

    with session_scope() as s:
        project = s.get(ProjectRow, args.project_id)
        if project is None or project.last_scan_id is None:
            print("No scan yet for this project.", file=sys.stderr)
            return 1
        rows = findings_service.visible_findings(
            s, args.project_id,
            severity=args.severity, status=args.status,
        )
        print(_table(
            [[f.id, f.severity, f.rule_id, f.category, f.status,
              f"{f.file_path}:{f.line_start or '-'}", f.title[:60]] for f in rows],
            ["id", "sev", "rule", "category", "status", "location", "title"],
        ))
        print(f"\n{len(rows)} finding(s). Use 'pyfix360 show <id>' for evidence.")
    return 0


def cmd_show(args) -> int:
    with session_scope() as s:
        f = s.get(FindingRow, args.finding_id)
        if f is None:
            print("finding not found", file=sys.stderr)
            return 1
        print(f"#{f.id} [{f.severity.upper()}] {f.title}")
        print(f"rule       : {f.rule_id}   tool: {f.tool}   confidence: {f.confidence}")
        print(f"status     : {f.status}   dedup: x{f.dedup_count}")
        print(f"location   : {f.file_path}:{f.line_start}-{f.line_end} ({f.symbol or 'module'})")
        print(f"group      : root_cause={f.root_cause_group}")
        print(f"description: {f.description}")
        for e in f.evidence:
            print(f"evidence ({e.kind}) {e.file_path}:{e.line_start}-{e.line_end}")
            if e.snippet:
                for line in e.snippet.splitlines():
                    print(f"    | {line}")
    return 0


def cmd_plan(args) -> int:
    with session_scope() as s:
        plan = repair_service.plan_repair(s, args.finding_id)
        print(f"Repair plan #{plan.id}: {plan.title}")
        print(f"  risk      : {plan.estimated_risk}")
        print(f"  rationale : {plan.rationale}")
        print("  steps:")
        for step in plan.steps:
            flag = " [requires approval]" if step.get("requires_approval") else ""
            print(f"    {step['order']}. {step['action']}: {step['detail']}{flag}")
        print(f"\nExecute with: pyfix360 repair {plan.id}")
    return 0


def _confirm_access(session, capability: str, action: str, *,
                    assume_yes: bool = False) -> bool:
    """Ask on the terminal what the interface asks in a dialog.

    FULL passes silently, ASK prompts, BLOCKED refuses. ``assume_yes`` is the
    ``--yes`` flag or an answer the operator has already given, standing in for
    the human's consent. The service re-checks the same level either way.
    """
    from pyfix360.models import AccessLevel

    level = settings_service.access_level(session, capability)
    if level is AccessLevel.BLOCKED:
        print(f"refused: '{capability}' is set to BLOCKED in Settings → Access "
              f"Control — {action} is not allowed.", file=sys.stderr)
        return False
    if level is AccessLevel.FULL or assume_yes:
        return True
    answer = input(f"{action} — '{capability}' is set to ASK. Proceed? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _confirm_approvals(session, plan_id: int, *, assume_yes: bool = False):
    """Ask for the extra approvals a repair needs. ``None`` means "no"."""
    try:
        needed = repair_service.required_approvals(session, plan_id)
    except repair_service.RepairError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return None
    if not needed:
        return set()
    if assume_yes:
        return set(needed)
    print("This change needs explicit approval for: " + ", ".join(sorted(needed)))
    answer = input("Approve? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        return None
    return set(needed)


def cmd_repair(args) -> int:
    cfg = get_config()
    with session_scope() as s:
        plan = s.get(RepairPlanRow, args.plan_id)
        if plan is None:
            print("plan not found", file=sys.stderr)
            return 1
        confirmed = bool(args.yes)
        if not confirmed:
            print(f"Plan: {plan.title}")
            print("This will snapshot the project and run tests in an isolated "
                  "workspace.")
            if args.apply:
                print("VERIFIED changes will be copied back to the ORIGINAL project.")
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return 1
            confirmed = True
        if args.apply and not _confirm_access(
                s, "apply_to_original",
                "Copy verified changes back to the ORIGINAL project",
                assume_yes=confirmed):
            return 1
        approvals = _confirm_approvals(s, args.plan_id, assume_yes=confirmed)
        if approvals is None:
            return 1
        try:
            result = repair_service.execute_repair(
                s, args.plan_id, timeout_s=cfg.execution_timeout_s,
                memory_limit_mb=cfg.memory_limit_mb, max_retries=cfg.max_repair_retries,
                apply_to_original=args.apply,
                confirmed=confirmed,
                approvals=approvals,
            )
        except repair_service.ApprovalRequired as exc:
            print(f"refused: {exc}", file=sys.stderr)
            print("Approve it with --yes after reading the plan, or turn the "
                  "requirement off in Settings → Repair & Safety.", file=sys.stderr)
            return 1
        print(f"Repair result: {result.status.value}  verification={result.verification}")
        if result.patch:
            print(f"Patch #{result.patch.id} on {result.patch.file_path} "
                  f"(+{result.patch.added_lines}/-{result.patch.removed_lines})")
            print("---- diff ----")
            print(result.patch.diff)
            print("--------------")
            print(f"explanation: {result.patch.explanation}")
        if result.baseline_test_run:
            b = result.baseline_test_run
            print(f"baseline tests : {b.outcome} (passed={b.passed} failed={b.failed} "
                  f"errors={b.errors})")
        if result.post_test_run:
            p = result.post_test_run
            print(f"post-patch     : {p.outcome} (passed={p.passed} failed={p.failed} "
                  f"errors={p.errors})")
        print(f"message        : {result.message}")
        if result.verification != "passed":
            print("NOTE: patch is NOT verified; do not treat it as safe.")
    return 0


def cmd_report(args) -> int:
    with session_scope() as s:
        if not _confirm_access(s, "export_reports", "Generate a report",
                               assume_yes=bool(args.yes)):
            return 1
        path = report_service.generate_report(
            s, args.project_id, fmt=args.format,
            out_path=Path(args.out) if args.out else None,
            confirmed=True,
        )
        print(f"Report written: {path}")
    return 0


def cmd_rollback(args) -> int:
    with session_scope() as s:
        if not _confirm_access(s, "rollback", f"Roll back patch #{args.patch_id}",
                               assume_yes=bool(args.yes)):
            return 1
        snap = repair_service.rollback_patch(s, args.patch_id, confirmed=True)
        print(f"Rolled back using snapshot #{snap.id} ({snap.label}).")
    return 0


def cmd_patches(args) -> int:
    with session_scope() as s:
        rows = repair_service.list_patches(s, args.project_id)
        print(_table(
            [[p.id, p.status, p.file_path, f"+{p.added_lines}/-{p.removed_lines}",
              p.risk, p.origin] for p in rows],
            ["id", "status", "file", "delta", "risk", "origin"],
        ))
    return 0


def cmd_features(args) -> int:
    criteria = None
    if args.criteria:
        criteria = Path(args.criteria).read_text(encoding="utf-8-sig").splitlines()
    with session_scope() as s:
        feature_service.analyze_completeness(s, args.project_id, criteria)
        matrix = feature_service.completeness_matrix(s, args.project_id)
        print(_table(
            [[m["name"][:40], m["status"], m["confidence"], m["description"][:50]]
             for m in matrix],
            ["feature", "status", "confidence", "evidence"],
        ))
        print(f"\n{len(matrix)} feature(s) in the completeness matrix.")
    return 0


def cmd_history(args) -> int:
    with session_scope() as s:
        rows = s.scalars(
            select(AuditEventRow).where(AuditEventRow.project_id == args.project_id)
            .order_by(AuditEventRow.id.desc()).limit(100)
        ).all()
        print(_table(
            [[e.id, str(e.timestamp), e.actor, e.risk, e.action, (e.target or "")[:30]]
             for e in rows],
            ["id", "timestamp", "actor", "risk", "action", "target"],
        ))
    return 0


def cmd_providers(args) -> int:
    with session_scope() as s:
        if args.provider_cmd == "list":
            rows = ai_provider_service.list_providers(s)
            print(_table(
                [[p.id, p.name, p.provider_type, p.privacy_mode, p.model,
                  "enabled" if p.enabled else "disabled"] for p in rows],
                ["id", "name", "type", "privacy", "model", "state"],
            ))
            info = store_info()
            print(f"\nCredential store backend: {info.backend} {info.warning}")
        elif args.provider_cmd == "presets":
            from pyfix360.ai.catalog import CATALOG_REVIEWED, all_presets

            print(f"Catalogue reviewed: {CATALOG_REVIEWED}")
            print("Availability changes often — verify before relying on it.\n")
            for preset in all_presets():
                key_note = "key required" if preset.requires_key else "no key"
                print(f"{preset.key:<12} {preset.label}  [{key_note}]")
                print(f"{'':<12} {preset.base_url or '(you supply the URL)'}")
                if preset.models:
                    for m in preset.models[:4]:
                        print(f"{'':<14}- {m.id}  ({m.cost}) {m.label}")
                    if len(preset.models) > 4:
                        print(f"{'':<14}… {len(preset.models) - 4} more")
                print()
        elif args.provider_cmd == "preset":
            row = ai_provider_service.create_from_preset(
                s, args.key, enable=args.enable, api_key=args.api_key,
                model=args.model)
            print(f"Saved provider #{row.id} ({row.name}) from preset "
                  f"'{args.key}'.")
            if row.enabled:
                print("State: enabled. A remote provider still needs an "
                      "explicit privacy mode before it will send anything.")
            else:
                print("State: disabled (starts off until you enable it).")
        elif args.provider_cmd == "add":
            cfg = AIProviderConfig(
                name=args.name, provider_type=ProviderType(args.type),
                base_url=args.url or "", model=args.model or "",
                timeout_s=args.timeout, retry_limit=args.retries,
                privacy_mode=PrivacyMode(args.privacy), enabled=not args.disabled,
            )
            row = ai_provider_service.upsert_provider(s, cfg, api_key=args.api_key)
            print(f"Saved provider #{row.id} ({row.name}).")
            if PrivacyMode(args.privacy) != PrivacyMode.LOCAL_ONLY and not row.base_url:
                print("WARNING: remote provider without explicit endpoint.")
        elif args.provider_cmd == "test":
            ok, msg = ai_provider_service.test_provider(s, args.id)
            print(("OK: " if ok else "") + msg)
            return 0 if ok else 1
        elif args.provider_cmd == "discover":
            if not _confirm_access(s, "remote_ai",
                                   f"Ask provider #{args.id} for its model list",
                                   assume_yes=bool(getattr(args, "yes", False))):
                return 1
            ok, detail, ids = ai_provider_service.refresh_models(
                s, args.id, confirmed=True)
            if ok:
                print(f"Discovered {len(ids)} model id(s):")
                for m in ai_provider_service.discovered_annotated(s, args.id):
                    print(f"  {m.id}  — {m.annotation}")
            else:
                print(f"Discovery failed: {detail}", file=sys.stderr)
                return 1
        elif args.provider_cmd == "remove":
            ai_provider_service.remove_provider(s, args.id)
            print(f"Removed provider #{args.id}.")
    return 0


def cmd_settings(args) -> int:
    with session_scope() as s:
        if args.settings_cmd == "set":
            st = settings_service.get_settings(s)
            key, value = args.key, args.value
            if not hasattr(st, key):
                print(f"unknown setting: {key}", file=sys.stderr)
                return 1
            try:
                setattr(st, key, settings_service.coerce(key, value))
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            settings_service.save_settings(s, st)
            print(f"{key} = {getattr(st, key)}")
        elif args.settings_cmd == "get":
            st = settings_service.get_settings(s)
            if not hasattr(st, args.key):
                print(f"unknown setting: {args.key}", file=sys.stderr)
                return 1
            value = getattr(st, args.key)
            if hasattr(value, "value"):  # enum
                value = value.value
            print(json.dumps(value, default=str) if isinstance(
                value, (dict, list)) else value)
        elif args.settings_cmd == "export":
            payload = settings_service.export_settings(s)
            out = Path(args.path)
            out.write_text(payload, encoding="utf-8")
            print(f"Wrote settings to {out}")
            print("Note: no credentials are included in this file.")
        elif args.settings_cmd == "import":
            payload = Path(args.path).read_text(encoding="utf-8")
            settings_service.import_settings(s, payload)
            print(f"Imported settings from {args.path}")
            settings_service.apply_to_config(settings_service.get_settings(s))
        elif args.settings_cmd == "reset":
            settings_service.reset_settings(s)
            print("Settings reset to defaults.")
        else:
            st = settings_service.get_settings(s)
            print(json.dumps(st.model_dump(mode="json"), indent=2))
    return 0


def cmd_gates(args) -> int:
    """List gates, or evaluate them against a project's latest scan."""
    from pyfix360.services import gate_service

    with session_scope() as s:
        if args.gate_cmd == "eval":
            report = gate_service.evaluate(s, args.project_id, args.scan)
            if not report.results:
                print("No gates configured for this project.")
                return 0
            for result in report.results:
                mark = "PASS" if result.passed else "FAIL"
                note = "" if result.blocking else " (advisory)"
                detail = result.detail or f"{result.observed} vs {result.expected}"
                print(f"[{mark}] {result.name}{note}: {detail}")
            print()
            print(gate_service.summary_line(report))
            return 0 if report.passed else 1
        # `pyfix360 gates` with no sub-command lists every gate, so the
        # project filter is optional here rather than a required argument.
        rows = gate_service.list_gates(s, project_id=getattr(args, "project_id", None))
        print(_table(
            [[g.id, g.metric, g.operator, g.threshold, g.severity or "-",
              "enabled" if g.enabled else "off",
              "blocking" if g.blocking else "advisory", g.name]
             for g in rows],
            ["id", "metric", "op", "value", "severity", "state", "mode", "name"],
        ))
    return 0


def cmd_scans(args) -> int:
    with session_scope() as s:
        rows = s.scalars(
            select(ScanRow).where(ScanRow.project_id == args.project_id).order_by(ScanRow.id)
        ).all()
        print(_table(
            [[sc.id, sc.status, sc.findings_count, sc.files_scanned,
              ", ".join(sc.tools_ran), str(sc.finished_at)] for sc in rows],
            ["id", "status", "findings", "files", "tools", "finished"],
        ))
    return 0


def cmd_update(args) -> int:
    """Check for a newer release and, when asked, install it."""
    from pyfix360.services import update_service

    # Typing the command *is* the confirmation: the check never happens on its
    # own here. The policy still decides whether the machine may talk to the
    # update server at all.
    try:
        with session_scope() as s:
            info = update_service.check(s, repo=args.repo, confirmed=True)
    except settings_service.AccessDenied as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except update_service.UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if info is None:
        print(f"{APP_NAME} {__version__} is the newest version.")
        return 0

    print(f"Version {info.version} is available; you are running {info.current}.")
    if info.notes:
        print()
        print(info.notes.strip()[:2000])
        print()
    if args.check_only:
        return 0

    if not getattr(args, "yes", False):
        answer = input("Install it now? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Left the installed version alone.")
            return 0

    print("Downloading the installer…")
    try:
        installer = update_service.download(info)
    except update_service.UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Verified {installer.name}; starting the installer.")
    with session_scope() as s:
        update_service.start_install(s, info, installer)
    return 0


def cmd_service(args) -> int:
    """Everything that runs with no window: the host, and the queue it works off."""
    from pyfix360.services import host_service

    action = args.service_cmd
    if action == "run":
        from pyfix360 import service_host

        return service_host.run_foreground(
            max_polls=args.polls if args.once else None, quiet=args.quiet)
    if action == "probe":
        for line in host_service.probe().lines():
            print(line)
        return 0
    if action == "logs":
        for line in host_service.log_tail(args.lines):
            print(line)
        return 0
    if action in ("jobs", "enqueue", "approve", "skip", "cancel"):
        return _service_queue(args, action)

    if action == "install":
        report = host_service.install(
            mode=args.mode, username=args.username, password=_service_password(args),
            start=not args.no_start)
    elif action == "uninstall":
        report = host_service.uninstall()
    elif action == "start":
        report = host_service.start()
    elif action == "stop":
        report = host_service.stop(force=args.force)
    else:
        report = host_service.status()
    for line in report.lines():
        print(line)
    if action == "status":
        counters = _service_jobs()
        for name, count in counters.items():
            if count:
                print(f"  {name}: {count}")
        # Running or not is the answer a script is asking for, so it is the exit code.
        return 0 if report.running else 1
    return 0 if report.ok else 1


def _service_password(args) -> str | None:
    """Ask for the password rather than reading it off the command line.

    ``schtasks /rp`` has to be given the password to store it, but a password in
    argv is one that the shell history and the process list both keep, so the only
    supported route is the prompt.
    """
    if not args.password_ask:
        return None
    import getpass

    user = args.username or getpass.getuser()
    return getpass.getpass(f"Windows password for {user}: ")


def _service_jobs() -> dict[str, int]:
    from pyfix360.services import job_service

    with session_scope() as s:
        return {name: count for name, count in job_service.counters(s).items()
                if count}


def _service_queue(args, action: str) -> int:
    from pyfix360.services import job_service

    with session_scope() as s:
        if action == "enqueue":
            job = job_service.enqueue(s, args.project_id, confirmed=args.confirmed,
                                      note=args.note or "")
            print(f"job #{job.id} queued ({job.kind}) for project "
                  f"{job.project_id}")
            print("  it starts the next time the background host polls; "
                  "`pyfix360 service status` says whether it is running")
            if not args.confirmed:
                print("  without --confirmed, a step that needs approval parks the "
                      "job rather than assuming you meant yes")
            return 0
        if action == "jobs":
            rows = job_service.list_jobs(s, open_only=args.open, limit=args.limit)
            print(_table(
                [[j.id, j.project_id, j.kind, j.status.value,
                  f"{j.progress[0]}/{j.progress[1]}",
                  (j.error or j.detail or "")[:48], str(j.updated_at)]
                 for j in rows],
                ["id", "proj", "kind", "status", "steps", "detail", "updated"],
            ))
            return 0
        if action in ("approve", "skip"):
            job = job_service.answer(s, args.job_id, action)
            if job is None:
                print(f"job #{args.job_id} not found", file=sys.stderr)
                return 1
            print(f"job #{job.id}: {action} recorded — the host takes it at its "
                  f"next poll")
            print(f"  status: {job.status.value} ({job.summary_line()})")
            return 0
        job = job_service.request_cancel(s, args.job_id)
        if job is None:
            print(f"job #{args.job_id} not found", file=sys.stderr)
            return 1
        print(f"job #{job.id}: {job.status.value}")
        if job.status.value == "running":
            print("  the cancel is recorded, not instant: a step already under way "
                  "runs to its end")
        return 0


# ------------------------------------------------------------------------------------
# parser
# ------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pyfix360",
                                description=f"{APP_NAME} — offline-first code inspection "
                                            "and repair platform")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="initialize the database and data directories")

    sp = sub.add_parser("import", help="import a project (folder, zip or git URL)")
    sp.add_argument("source")
    sp.add_argument("--origin", choices=["folder", "zip", "git"])
    sp.add_argument("--name")

    sub.add_parser("projects", help="list imported projects")

    sp = sub.add_parser("scan", help="run a scan on a project")
    sp.add_argument("project_id", type=int)
    sp.add_argument("--full", action="store_true", help="disable incremental scanning")
    sp.add_argument("--tools", help="comma-separated tool list (default: builtin + available)")

    sp = sub.add_parser("scans", help="list scans for a project")
    sp.add_argument("project_id", type=int)

    sp = sub.add_parser("findings", help="list findings of the latest scan")
    sp.add_argument("project_id", type=int)
    sp.add_argument("--severity", choices=["critical", "high", "medium", "low", "info"])
    sp.add_argument("--status")

    sp = sub.add_parser("show", help="show one finding with evidence")
    sp.add_argument("finding_id", type=int)

    sp = sub.add_parser("plan", help="create a repair plan for a finding")
    sp.add_argument("finding_id", type=int)

    sp = sub.add_parser("repair", help="execute a repair plan in an isolated workspace")
    sp.add_argument("plan_id", type=int)
    sp.add_argument("--apply", action="store_true",
                    help="copy verified changes back to the original project")
    sp.add_argument("--yes", action="store_true", help="skip confirmation")

    sp = sub.add_parser("patches", help="list patches for a project")
    sp.add_argument("project_id", type=int)

    sp = sub.add_parser("rollback", help="roll an applied patch back via its snapshot")
    sp.add_argument("patch_id", type=int)
    sp.add_argument("--yes", action="store_true", help="skip confirmation")

    sp = sub.add_parser("report", help="generate an audit report")
    sp.add_argument("project_id", type=int)
    sp.add_argument("--format", default="markdown", choices=["markdown", "json", "html"])
    sp.add_argument("--out")
    sp.add_argument("--yes", action="store_true", help="skip confirmation")

    sp = sub.add_parser("features", help="feature completeness matrix")
    sp.add_argument("project_id", type=int)
    sp.add_argument("--criteria", help="text file with acceptance criteria (one per line)")

    sp = sub.add_parser("history", help="audit trail for a project")
    sp.add_argument("project_id", type=int)

    sp = sub.add_parser("providers", help="manage AI providers")
    psub = sp.add_subparsers(dest="provider_cmd", required=True)
    psub.add_parser("list")
    psub.add_parser("presets", help="show the curated provider catalogue")
    a = psub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--type", required=True,
                   choices=[t.value for t in ProviderType])
    a.add_argument("--url")
    a.add_argument("--model")
    a.add_argument("--api-key")
    a.add_argument("--privacy", default="local_only",
                   choices=[m.value for m in PrivacyMode])
    a.add_argument("--timeout", type=int, default=60)
    a.add_argument("--retries", type=int, default=2)
    a.add_argument("--disabled", action="store_true")
    pa = psub.add_parser("preset", help="create a provider from a catalogue preset")
    pa.add_argument("key")
    pa.add_argument("--enable", action="store_true")
    pa.add_argument("--api-key")
    pa.add_argument("--model")
    t = psub.add_parser("test")
    t.add_argument("id", type=int)
    d = psub.add_parser("discover", help="ask a provider for its live model list")
    d.add_argument("id", type=int)
    d.add_argument("--yes", action="store_true", help="skip confirmation")
    r = psub.add_parser("remove")
    r.add_argument("id", type=int)

    sp = sub.add_parser("settings", help="show or update settings")
    ssub = sp.add_subparsers(dest="settings_cmd")
    ssub.add_parser("show")
    st = ssub.add_parser("set")
    st.add_argument("key")
    st.add_argument("value")
    sg = ssub.add_parser("get")
    sg.add_argument("key")
    se = ssub.add_parser("export")
    se.add_argument("path")
    si = ssub.add_parser("import")
    si.add_argument("path")
    ssub.add_parser("reset")

    sp = sub.add_parser("gates", help="list quality gates or evaluate them")
    gsub = sp.add_subparsers(dest="gate_cmd")
    gl = gsub.add_parser("list")
    gl.add_argument("project_id", type=int, nargs="?", default=None)
    ge = gsub.add_parser("eval")
    ge.add_argument("project_id", type=int)
    ge.add_argument("--scan", type=int, default=None)

    sp = sub.add_parser("update", help="check for and install a new version")
    sp.add_argument("--check", dest="check_only", action="store_true",
                    help="only report what is available")
    sp.add_argument("--yes", action="store_true",
                    help="install without asking first")
    sp.add_argument("--repo", default=None,
                    help="the repository to check (default: the one that "
                         "published this build)")

    from pyfix360.services import host_service

    sp = sub.add_parser("service",
                        help="the background host: install it, or work its queue")
    svc = sp.add_subparsers(dest="service_cmd", required=True)
    svc.add_parser("probe", help="what this machine allows, and what each mode costs")
    svci = svc.add_parser("install", help="register the host so it starts by itself")
    svci.add_argument("--mode", choices=list(host_service.MODES),
                      help="default: the most persistent mode this machine allows")
    svci.add_argument("--username", help="account for --mode task-account")
    svci.add_argument("--password-ask", dest="password_ask", action="store_true",
                      help="prompt for the password to store (it never belongs in "
                           "a command line)")
    svci.add_argument("--no-start", action="store_true",
                      help="register it, but leave it stopped")
    svc.add_parser("uninstall", help="remove the registration, keeping job history")
    svc.add_parser("start", help="start what install registered")
    svc_stop = svc.add_parser("stop", help="ask the host to finish and exit")
    svc_stop.add_argument("--force", action="store_true",
                          help="end the process now, even mid-step")
    svc.add_parser("status", help="what is installed, and whether it is running")
    svc_logs = svc.add_parser("logs", help="the tail of the host log")
    svc_logs.add_argument("-n", "--lines", type=int, default=40)
    svc_run = svc.add_parser("run",
                             help="poll the queue in this terminal, nothing installed")
    svc_run.add_argument("--once", action="store_true",
                        help="a bounded number of polls")
    svc_run.add_argument("--polls", type=int, default=3)
    svc_run.add_argument("--quiet", action="store_true", help="log to the file only")
    svc_q = svc.add_parser("enqueue", help="put an autopilot run on the queue")
    svc_q.add_argument("project_id", type=int)
    svc_q.add_argument("--confirmed", action="store_true",
                       help="you are answering the approval gate now, for a step "
                            "that needs one")
    svc_q.add_argument("--note")
    svc_jobs = svc.add_parser("jobs", help="the queue, as the host sees it")
    svc_jobs.add_argument("--open", action="store_true", help="hide finished jobs")
    svc_jobs.add_argument("--limit", type=int, default=20)
    for name, help_text in (("approve", "say yes to a parked approval gate"),
                            ("skip", "say no to a parked gate and let the run go on"),
                            ("cancel", "ask for a job to be abandoned")):
        svc_answer = svc.add_parser(name, help=help_text)
        svc_answer.add_argument("job_id", type=int)

    return p


COMMANDS = {
    "init": cmd_init, "import": cmd_import, "projects": cmd_projects,
    "scan": cmd_scan, "scans": cmd_scans, "findings": cmd_findings,
    "show": cmd_show, "plan": cmd_plan, "repair": cmd_repair,
    "patches": cmd_patches, "rollback": cmd_rollback, "report": cmd_report,
    "features": cmd_features, "history": cmd_history, "providers": cmd_providers,
    "settings": cmd_settings, "gates": cmd_gates, "service": cmd_service,
    "update": cmd_update,
}


def _console_safe() -> None:
    """Make sure a message is never lost to the encoding of a Windows console.

    The services write "Settings → Access Control" and similar, and the arrow is
    not in cp1252: printing it would raise, and the operator would get a traceback
    instead of the remedy they came to read. Replacing the glyph loses far less
    than losing the line.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _console_safe()
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
