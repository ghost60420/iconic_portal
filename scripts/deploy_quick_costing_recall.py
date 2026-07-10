#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path


MIGRATION_APP = "crm"
MIGRATION_NUMBER = "0184"
MIGRATION_NAME = "0184_quick_costing_recall_workflow"
PREVIOUS_PRODUCTION_REF = "codex/quick-costing-shipping-included"

APPROVED_FILES = {
    "crm/migrations/0184_quick_costing_recall_workflow.py",
    "crm/models.py",
    "crm/services/costing_workflow.py",
    "crm/services/local_sewing.py",
    "crm/services/pipeline.py",
    "crm/services/production_orders.py",
    "crm/services/sales_attribution.py",
    "crm/services/workflow_visibility.py",
    "crm/templates/crm/ceo_dashboard.html",
    "crm/templates/crm/ceo_executive_dashboard.html",
    "crm/templates/crm/costing/quick_costing_detail.html",
    "crm/templates/crm/costing/quick_quotation_client.html",
    "crm/templates/crm/invoice/invoice_view.html",
    "crm/templates/crm/production_detail.html",
    "crm/tests/test_quick_costing.py",
    "crm/urls.py",
    "crm/views.py",
    "crm/views_costing.py",
    "crm/views_invoice.py",
    "scripts/deploy_quick_costing_recall.py",
    "scripts/quick_costing_recall_metrics.py",
    "scripts/quick_costing_recall_page_health.py",
    "scripts/quick_costing_recall_smoke.py",
    "static/crm/production_detail.css",
}


class DeploymentFailure(Exception):
    pass


def run(command, *, stdin_path=None, check=True):
    printable = " ".join(str(part) for part in command)
    if stdin_path:
        printable = f"{printable} < {stdin_path}"
    print(f"+ {printable}", flush=True)
    stdin = open(stdin_path, "r", encoding="utf-8") if stdin_path else None
    try:
        result = subprocess.run(
            [str(part) for part in command],
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    finally:
        if stdin:
            stdin.close()
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if check and result.returncode:
        raise DeploymentFailure(f"Command failed ({result.returncode}): {printable}")
    return result


def manage(args, *, python, settings=None, stdin_path=None, check=True):
    command = [python, "manage.py", *args]
    if settings:
        command.append(f"--settings={settings}")
    return run(command, stdin_path=stdin_path, check=check)


def parse_json_output(output):
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise DeploymentFailure("No JSON metrics snapshot was found in command output.")


def capture_metrics(args):
    result = manage(
        ["shell"],
        python=args.python,
        settings=args.settings,
        stdin_path="scripts/quick_costing_recall_metrics.py",
    )
    return parse_json_output(result.stdout)


def compare_metrics(before, after):
    checks = {
        "COUNTS_MATCH": before["counts"] == after["counts"],
        "INVOICE_TOTALS_MATCH": before["invoice_totals"] == after["invoice_totals"],
        "PAYMENT_TOTALS_MATCH": before["payment_totals"] == after["payment_totals"],
        "ACCOUNTING_TOTALS_MATCH": before["accounting_totals"] == after["accounting_totals"],
        "QUICK_COSTING_TOTALS_MATCH": before["quick_costing_totals"] == after["quick_costing_totals"],
    }
    for label, passed in checks.items():
        print(f"{label}= {passed}")
    if not all(checks.values()):
        print("METRICS_BEFORE=", json.dumps(before, sort_keys=True))
        print("METRICS_AFTER=", json.dumps(after, sort_keys=True))
        raise DeploymentFailure("Counts or financial totals changed.")


def verify_git_scope(args):
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    changed = set(
        run(["git", "diff", "--name-only", f"{args.previous_ref}..HEAD"])
        .stdout.strip()
        .splitlines()
    )
    changed.discard("")
    unexpected = sorted(changed - APPROVED_FILES)
    print(f"GIT_BRANCH= {branch}")
    print(f"GIT_HEAD= {head}")
    print("DEPLOY_FILES=", "\n".join(sorted(changed)) if changed else "(none)")
    if unexpected:
        raise DeploymentFailure(f"Unexpected files in deploy diff: {unexpected}")
    return branch, head


def verify_migration_preconditions(args):
    result = manage(["showmigrations", MIGRATION_APP], python=args.python, settings=args.settings)
    output = result.stdout
    if f"[X] 0183_" not in output:
        raise DeploymentFailure("Migration 0183 is not applied.")
    if f"[ ] {MIGRATION_NAME}" not in output:
        raise DeploymentFailure("Migration 0184 is not visible as unapplied.")
    print("MIGRATION_PRECHECK_OK")


def verify_migration_live(args):
    result = manage(["showmigrations", MIGRATION_APP], python=args.python, settings=args.settings)
    if f"[X] {MIGRATION_NAME}" not in result.stdout:
        raise DeploymentFailure("Migration 0184 is not applied after migrate.")
    print("MIGRATION_0184_APPLIED_OK")


def verify_foreign_keys(args):
    result = manage(
        [
            "shell",
            "-c",
            (
                "from django.db import connection; "
                "cursor=connection.cursor(); cursor.execute('PRAGMA foreign_key_check'); "
                "rows=cursor.fetchall(); print('FOREIGN_KEY_ERRORS=', len(rows)); "
                "raise SystemExit(1 if rows else 0)"
            ),
        ],
        python=args.python,
        settings=args.settings,
    )
    if "FOREIGN_KEY_ERRORS= 0" not in result.stdout:
        raise DeploymentFailure("Foreign key check did not report zero errors.")


def verify_smoke(args):
    result = manage(
        ["shell"],
        python=args.python,
        settings=args.settings,
        stdin_path="scripts/quick_costing_recall_smoke.py",
    )
    required = [
        "QUICK_COSTING_RECALL_SMOKE_OK",
        "FOREIGN_KEY_ERRORS_AFTER_SMOKE= 0",
    ]
    for marker in required:
        if marker not in result.stdout:
            raise DeploymentFailure(f"Smoke output missing marker: {marker}")


def verify_page_health(args):
    result = manage(
        ["shell"],
        python=args.python,
        settings=args.settings,
        stdin_path="scripts/quick_costing_recall_page_health.py",
    )
    if "PAGE_HEALTH_OK" not in result.stdout:
        raise DeploymentFailure("Page health output missing PAGE_HEALTH_OK.")


def create_backup(args):
    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    label = "live" if args.mode == "production" else "rehearsal"
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"db_before_0184_{label}_{timestamp}.sqlite3"
    shutil.copy2(args.database_path, backup_path)
    size = backup_path.stat().st_size
    print(f"BACKUP_PATH= {backup_path}")
    print(f"BACKUP_SIZE= {size}")
    if size <= 0:
        raise DeploymentFailure("Backup size is zero.")
    return backup_path


def restore_on_failure(args, backup_path):
    if args.mode != "production":
        print("REHEARSAL_FAILURE_NO_PRODUCTION_ROLLBACK")
        return
    print("PRODUCTION_RESTORE_START")
    shutil.copy2(backup_path, args.database_path)
    run(["git", "checkout", args.previous_ref], check=False)
    run(["git", "pull", "--ff-only", "origin", args.previous_ref], check=False)
    if args.restart_service:
        run(["sudo", "systemctl", "restart", args.restart_service], check=False)
    print("PRODUCTION_RESTORE_DONE")


def restart_service_if_requested(args):
    if not args.restart_service:
        print("SERVICE_RESTART_SKIPPED")
        return
    run(["sudo", "systemctl", "restart", args.restart_service])
    status = run(["systemctl", "is-active", args.restart_service]).stdout.strip()
    print(f"SERVICE_STATUS= {status}")
    if status != "active":
        raise DeploymentFailure(f"{args.restart_service} is not active after restart.")


def build_parser():
    parser = argparse.ArgumentParser(description="Deploy Quick Costing recall migration 0184 safely.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rehearsal", action="store_const", const="rehearsal", dest="mode")
    mode.add_argument("--production", action="store_const", const="production", dest="mode")
    parser.add_argument("--python", default="venv/bin/python")
    parser.add_argument("--settings", default=None)
    parser.add_argument("--database-path", default="db.sqlite3")
    parser.add_argument("--previous-ref", default=PREVIOUS_PRODUCTION_REF)
    parser.add_argument("--restart-service", default="")
    return parser


def main():
    args = build_parser().parse_args()
    if not Path(args.python).exists() and args.python == "venv/bin/python":
        args.python = "python3"

    backup_path = None
    try:
        print(f"DEPLOYMENT_MODE= {args.mode}")
        verify_git_scope(args)
        verify_migration_preconditions(args)
        backup_path = create_backup(args)
        before_metrics = capture_metrics(args)
        print("METRICS_BEFORE=", json.dumps(before_metrics, sort_keys=True))
        manage(["migrate", MIGRATION_APP, MIGRATION_NUMBER], python=args.python, settings=args.settings)
        verify_migration_live(args)
        manage(["check"], python=args.python, settings=args.settings)
        verify_foreign_keys(args)
        after_metrics = capture_metrics(args)
        print("METRICS_AFTER=", json.dumps(after_metrics, sort_keys=True))
        compare_metrics(before_metrics, after_metrics)
        verify_smoke(args)
        verify_page_health(args)
        restart_service_if_requested(args)
    except Exception as exc:
        print(f"DEPLOYMENT_FAILURE= {exc}")
        if backup_path:
            restore_on_failure(args, backup_path)
        raise

    print("DEPLOYMENT_OK")
    print(f"BACKUP_PATH= {backup_path}")


if __name__ == "__main__":
    main()
