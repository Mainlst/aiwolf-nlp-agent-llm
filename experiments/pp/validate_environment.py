#!/usr/bin/env python3
"""Run the reproducible, API-free PP environment preflight and save evidence."""

# ruff: noqa: S603, S607, T201

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "experiments" / "validation" / "pp-environment-preflight.json"


def run(command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run one API-free check and retain only status and the final output line."""
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    return {"command": " ".join(command), "returncode": result.returncode, "last_line": lines[-1] if lines else ""}


def key_presence() -> dict[str, bool]:
    """Return configured key names and booleans without exposing values."""
    env_path = ROOT / "config" / ".env"
    expected = {"OPENAI_API_KEY"}
    present: set[str] = set()
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() in expected and value.strip().strip("\"'"):
                present.add(name.strip())
    return {name: name in present for name in sorted(expected)}


def main() -> int:
    """Execute tests and static checks, then save a secret-free JSON report."""
    plan_path = ROOT / "experiments" / "pp" / "pilot" / "plan.yml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    access_path = ROOT / "experiments" / "validation" / "openai-access.json"
    access = json.loads(access_path.read_text(encoding="utf-8")) if access_path.is_file() else {}
    connection_path = ROOT / "experiments" / "validation" / "pp-luna-connection.json"
    connection = json.loads(connection_path.read_text(encoding="utf-8")) if connection_path.is_file() else {}
    test_env = os.environ.copy()
    test_env["PYTHONPATH"] = "src"
    checks = {
        "unit_tests": run([sys.executable, "-m", "unittest", "discover", "-v"], test_env),
        "saved_win_role": run([sys.executable, "experiments/evaluation/verify_saved_logs.py"]),
        "saved_pp_semantics": run([sys.executable, "experiments/evaluation/verify_pp_saved_log.py"]),
        "pilot_static": run([sys.executable, "experiments/pp/check_pilot.py"]),
    }
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    git_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    backup = Path(plan["backup"]["destination"])
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "api_calls_made": False,
        "checks": checks,
        "keys": {"file": "config/.env", "presence_only": key_presence(), "values_recorded": False},
        "model": {
            "id": plan["fixed_runtime"]["model"],
            "official_catalog_status": "documented",
            "account_access": access,
            "pricing": plan["pricing_usd_per_million_tokens"],
        },
        "budget": plan["budget"],
        "paid_connection_check": connection,
        "backup": {
            "destination": str(backup),
            "exists": backup.is_dir(),
            "writable": os.access(backup, os.W_OK),
        },
        "server": {
            "config_exists": (ROOT.parent / "aiwolf-nlp-server" / "config" / "default_en_5.yml").is_file(),
            "binary_exists": (ROOT.parent / "aiwolf-nlp-server-linux-amd64").is_file(),
        },
        "repository": {"commit": git_commit, "dirty": bool(git_status), "status": git_status},
    }
    report["passed_api_free_checks"] = all(item["returncode"] == 0 for item in checks.values())
    report["ready_for_paid_pilot"] = bool(
        report["passed_api_free_checks"]
        and all(report["keys"]["presence_only"].values())
        and bool(access.get("model_available"))
        and bool(connection.get("passed"))
        and report["backup"]["exists"]
        and report["backup"]["writable"]
        and (
            plan["budget"]["remaining_usd"] is not None
            or plan["budget"]["remaining_budget_status"] == "unavailable_user_authorized_skip"
        )
        and not report["repository"]["dirty"],
    )
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"API-free checks passed={report['passed_api_free_checks']} "
        f"ready_for_paid_pilot={report['ready_for_paid_pilot']}",
    )
    return 0 if report["passed_api_free_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
