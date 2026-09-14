#!/usr/bin/env python3
"""Freeze all eligibility decisions before condition-specific outcomes are viewed."""

# ruff: noqa: D103, EM102, T201, TRY003

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    plan = yaml.safe_load((run_dir / "plan.yml").read_text(encoding="utf-8"))
    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        assignments = list(csv.DictReader(handle))
    decisions: dict[str, dict[str, object]] = {}
    audit_hashes: dict[str, str] = {}
    blocks = sorted({row["analysis_block"] for row in assignments})
    for block in blocks:
        path = run_dir / "block-audits" / f"block-{block}.json"
        if not path.is_file():
            raise FileNotFoundError(f"missing block audit: {path}")
        audit = json.loads(path.read_text(encoding="utf-8"))
        if not audit.get("passed"):
            raise RuntimeError(f"block {block} did not pass")
        backup = (
            Path(plan["backup"]["destination"]).resolve()
            / run_dir.name
            / f"block-{block}"
            / "backup-manifest.json"
        )
        if not backup.is_file():
            raise FileNotFoundError(f"missing block backup: {backup}")
        audit_hashes[block] = sha256(path)
        for row in audit["rows"]:
            decisions[row["row_id"]] = {
                "eligible": bool(row["eligible"]),
                "reasons": list(row["reasons"]),
                "analysis_block": block,
            }
    expected = {row["row_id"] for row in assignments}
    if set(decisions) != expected:
        raise RuntimeError(f"eligibility coverage mismatch: assignments={len(expected)} decisions={len(decisions)}")
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "blinded": True,
        "condition_specific_outcomes_viewed": False,
        "run_id": run_dir.name,
        "planned_games": len(assignments),
        "eligible_games": sum(bool(item["eligible"]) for item in decisions.values()),
        "excluded_games": sum(not bool(item["eligible"]) for item in decisions.values()),
        "block_audit_sha256": audit_hashes,
        "decisions": decisions,
    }
    output = run_dir / "eligibility-frozen.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite frozen eligibility: {output}")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"eligibility={output} eligible={report['eligible_games']} "
        f"excluded={report['excluded_games']} sha256={sha256(output)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
