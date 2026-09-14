#!/usr/bin/env python3
"""Run normal games in sequentially audited blocks with parallel games inside each block."""

# ruff: noqa: D103, EM101, EM102, PLR2004, S603, T201, TRY003

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]


def validate_run_server_binary(plan: dict[str, object], manifest: dict[str, object]) -> None:
    """Reject retired or binary-mismatched format-comparison runs before spawning."""
    expected = plan.get("required_server_binary_sha256")
    if plan.get("formats") and not expected:
        raise ValueError("Format-comparison run is retired: no required server binary SHA-256")
    server = manifest.get("server", {})
    frozen = server.get("binary_sha256") if isinstance(server, dict) else None
    if expected and frozen != expected:
        raise ValueError("Frozen server binary does not match the run plan")


def run(command: list[str], *, check: bool = True) -> int:
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, check=check).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--first-block", type=int, default=1)
    parser.add_argument("--last-block", type=int, default=20)
    parser.add_argument("--max-parallel", type=int, default=20)
    parser.add_argument("--base-port", type=int, default=18080)
    parser.add_argument("--game-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    plan = yaml.safe_load((run_dir / "plan.yml").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    validate_run_server_binary(plan, manifest)
    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    maximum_block = max(int(row["analysis_block"]) for row in all_rows)
    if not 1 <= args.first_block <= args.last_block <= maximum_block:
        raise ValueError(f"block range must be within 1..{maximum_block}")
    if args.max_parallel < 2:
        raise ValueError("normal-game execution must remain parallel; --max-parallel must be at least 2")
    for number in range(args.first_block, args.last_block + 1):
        block = f"{number:02d}"
        command = [
            sys.executable,
            str(ROOT / "experiments" / "runtime" / "run_parallel.py"),
            str(run_dir),
            "--confirm-pool",
            "O",
            "--analysis-block",
            block,
            "--max-parallel",
            str(args.max_parallel),
            "--base-port",
            str(args.base_port),
            "--game-timeout-seconds",
            str(args.game_timeout_seconds),
        ]
        if args.dry_run:
            command.append("--dry-run")
        runner_returncode = run(command, check=False)
        if args.dry_run:
            if runner_returncode != 0:
                raise RuntimeError("parallel dry-run failed")
            continue
        run([sys.executable, str(Path(__file__).with_name("audit_block.py")), str(run_dir), block])
        if runner_returncode != 0:
            raise RuntimeError("parallel block runner failed despite a passing audit")
        run([sys.executable, str(Path(__file__).with_name("backup_block.py")), str(run_dir), block])
    if not args.dry_run and args.first_block == 1 and args.last_block == maximum_block:
        run([sys.executable, str(Path(__file__).with_name("finalize_eligibility.py")), str(run_dir)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
