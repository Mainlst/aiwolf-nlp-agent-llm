#!/usr/bin/env python3
"""Create a recoverable, checksummed backup for one completed block."""

# ruff: noqa: C901, D103, EM102, T201, TRY003

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import yaml


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("analysis_block")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    block = f"{int(args.analysis_block):02d}"
    plan = yaml.safe_load((run_dir / "plan.yml").read_text(encoding="utf-8"))
    audit = json.loads((run_dir / "block-audits" / f"block-{block}.json").read_text(encoding="utf-8"))
    if not audit.get("passed"):
        raise RuntimeError(f"refusing to back up failed block {block}")
    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["analysis_block"] == block]
    destination = Path(plan["backup"]["destination"]).resolve() / run_dir.name / f"block-{block}"
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite block backup: {destination}")
    destination.mkdir(parents=True)
    for name in ("plan.yml", "assignments.csv", "manifest.json", "execution.jsonl", "parallel-execution.jsonl"):
        source = run_dir / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    shutil.copytree(run_dir / "block-audits", destination / "block-audits")
    for row in rows:
        row_id = row["row_id"]
        for relative in (
            Path("server-logs") / row_id,
            Path("runtime-configs") / row_id,
            Path("events") / row_id,
        ):
            source = run_dir / relative
            target = destination / relative
            if source.exists():
                shutil.copytree(source, target)
        target_log = run_dir / "agent-logs" / row["condition"] / row_id
        if target_log.exists():
            shutil.copytree(target_log, destination / "agent-logs" / row["condition"] / row_id)
        for team in plan["opponent_O"]["agents"]:
            opponent_log = run_dir / "agent-logs" / "opponents" / team / row_id
            if opponent_log.exists():
                shutil.copytree(opponent_log, destination / "agent-logs" / "opponents" / team / row_id)
    files = sorted(path for path in destination.rglob("*") if path.is_file())
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "run_id": run_dir.name,
        "analysis_block": block,
        "source": str(run_dir),
        "files": {str(path.relative_to(destination)): sha256(path) for path in files},
    }
    manifest_path = destination / "backup-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"backup={destination} files={len(files)} manifest_sha256={sha256(manifest_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
