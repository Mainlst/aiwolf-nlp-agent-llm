#!/usr/bin/env python3
"""API-free readiness check for the conflict-resolved PP v2 rerun."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
CONDITIONS = ROOT / "experiments" / "pp" / "v2" / "generated"
CORPUS = ROOT / "experiments" / "pp" / "fixed_boards_v2" / "generated"
EVALUATION_SPEC = ROOT / "experiments" / "pp" / "v2" / "evaluation-spec.yml"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    issues: list[str] = []

    condition_manifest = json.loads((CONDITIONS / "manifest.json").read_text(encoding="utf-8"))
    for condition, metadata in condition_manifest["configs"].items():
        path = CONDITIONS / metadata["file"]
        if sha256(path) != metadata["sha256"]:
            issues.append(f"condition hash mismatch: {condition}")
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        if config["openai"]["model"] != "gpt-5.6-luna" or config["openai"]["temperature"] != 1.0:
            issues.append(f"unexpected model or temperature: {condition}")
        if config["experiment"]["intervention_version"] != "v2-conflict-resolved":
            issues.append(f"unexpected intervention version: {condition}")
    if condition_manifest["evaluation_spec_sha256"] != sha256(EVALUATION_SPEC):
        issues.append("condition manifest evaluation-spec hash mismatch")
    audit = json.loads((CONDITIONS / "audit" / "audit.json").read_text(encoding="utf-8"))
    if audit.get("status") != "pass" or audit.get("expanded_prompt_count") != 8:
        issues.append("expanded prompt audit is not a complete pass")

    corpus_manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in corpus_manifest["files"].items():
        if sha256(CORPUS / name) != expected:
            issues.append(f"corpus hash mismatch: {name}")
    if corpus_manifest["condition_manifest_sha256"] != sha256(CONDITIONS / "manifest.json"):
        issues.append("corpus is not bound to the current v2 condition manifest")
    if corpus_manifest["evaluation_spec_sha256"] != sha256(EVALUATION_SPEC):
        issues.append("corpus is not bound to the current v2 evaluation spec")

    inputs = read_jsonl(CORPUS / "inputs.jsonl")
    confirmation = read_jsonl(CORPUS / "confirmation-inputs.jsonl")
    if len(inputs) != 120 or len({row["input_sha256"] for row in inputs}) != 120:
        issues.append("analysis corpus must contain 120 unique inputs")
    if len(confirmation) != 12:
        issues.append("route-check corpus must contain 12 inputs")

    key_present = bool(dotenv_values(ROOT / "config" / ".env").get("OPENAI_API_KEY"))
    if not key_present:
        issues.append("OPENAI_API_KEY is absent from config/.env")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "config/.env"], cwd=ROOT, check=False
    ).returncode == 0
    if not ignored:
        issues.append("config/.env is not ignored by Git")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    if args.require_clean and status:
        issues.append("worktree is dirty; commit the frozen rerun artifacts before paid execution")

    report = {
        "schema_version": 1,
        "study_id": "aiwolfdial-2026-pp-v2",
        "api_called": False,
        "artifact_checks_passed": not [issue for issue in issues if not issue.startswith("worktree is dirty")],
        "ready_for_paid_execution": not issues and not status,
        "credential_present": key_present,
        "credential_value_recorded": False,
        "config_env_gitignored": ignored,
        "worktree_clean": not status,
        "dirty_entry_count": len(status),
        "analysis_scenarios": len(inputs),
        "analysis_units": len(inputs) * 2 * 2,
        "generation_calls": len(inputs) * 2 * 2 * 2,
        "route_check_scenarios": len(confirmation),
        "route_check_calls": len(confirmation) * 2 * 2,
        "judge_calls_after_generation": len(inputs) * 2 * 2 * 3,
        "issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
