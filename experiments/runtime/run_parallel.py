#!/usr/bin/env python3
"""Run frozen pilot rows concurrently on isolated ports and log directories."""

from __future__ import annotations

import argparse
import csv
import json
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def port_is_available(host: str, port: int) -> bool:
    """Check that a local TCP port can be bound before starting the batch."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return True
    except OSError:
        return False


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one runner event."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    """Launch every planned row with bounded concurrency."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--confirm-pool", required=True, choices=("O",))
    parser.add_argument("--max-parallel", type=int, default=10)
    parser.add_argument("--base-port", type=int, default=18080)
    parser.add_argument("--game-timeout-seconds", type=float, default=900.0)
    parser.add_argument(
        "--analysis-block",
        help="run exactly one frozen analysis_block (for example 01)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.max_parallel < 1:
        raise ValueError("--max-parallel must be at least 1")
    run_dir = args.run_dir.resolve()
    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: int(row["run_order"]))
    if args.analysis_block is not None:
        rows = [row for row in rows if row.get("analysis_block") == args.analysis_block]
    if not rows:
        raise ValueError("No pilot rows found")
    if args.analysis_block is not None:
        balance_field = "format" if rows and rows[0].get("format") else "condition"
        expected_levels = ("turn_based", "anytime") if balance_field == "format" else ("P+", "P-PP")
        levels = {level: sum(row[balance_field] == level for row in rows) for level in expected_levels}
        if len(rows) != 20 or levels != dict.fromkeys(expected_levels, 10):
            raise ValueError(
                f"analysis block {args.analysis_block} must contain 20 rows balanced 10/10 by "
                f"{balance_field}; got {levels}",
            )

    ports = {row["row_id"]: args.base_port + index for index, row in enumerate(rows)}
    invalid = [port for port in ports.values() if not 1 <= port <= 65535]
    if invalid:
        raise ValueError(f"Invalid assigned ports: {invalid}")
    occupied = [port for port in ports.values() if not port_is_available("127.0.0.1", port)]
    deadline = time.monotonic() + 90.0
    while occupied and not args.dry_run and time.monotonic() < deadline:
        time.sleep(1.0)
        occupied = [port for port in ports.values() if not port_is_available("127.0.0.1", port)]
    if occupied and not args.dry_run:
        raise RuntimeError(f"Pilot ports are already in use: {occupied}")

    events_dir = run_dir / ("dry-run-events" if args.dry_run else "events")
    events_dir.mkdir(exist_ok=True)
    runner_events = run_dir / ("dry-run-parallel-execution.jsonl" if args.dry_run else "parallel-execution.jsonl")
    append_jsonl(
        runner_events,
        {
            "event": "batch_start",
            "timestamp": datetime.now(UTC).astimezone().isoformat(),
            "max_parallel": args.max_parallel,
            "ports": ports,
            "dry_run": args.dry_run,
            "analysis_block": args.analysis_block,
        },
    )

    def run_row(row: dict[str, str]) -> dict[str, Any]:
        row_id = row["row_id"]
        command = [
            sys.executable,
            str(ROOT / "experiments" / "runtime" / "run_one.py"),
            str(run_dir),
            row_id,
            "--confirm-pool",
            args.confirm_pool,
            "--port",
            str(ports[row_id]),
            "--game-timeout-seconds",
            str(args.game_timeout_seconds),
        ]
        if args.dry_run:
            command.append("--dry-run")
        output_path = events_dir / f"{row_id}-runner.txt"
        started = datetime.now(UTC).astimezone()
        with output_path.open("w", encoding="utf-8") as output:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        finished = datetime.now(UTC).astimezone()
        return {
            "row_id": row_id,
            "port": ports[row_id],
            "returncode": completed.returncode,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "elapsed_seconds": round((finished - started).total_seconds(), 3),
            "output": str(output_path),
        }

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(args.max_parallel, len(rows))) as executor:
        futures = {executor.submit(run_row, row): row["row_id"] for row in rows}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            append_jsonl(runner_events, {"event": "row_finish", **result})
            print(f"{result['row_id']}: returncode={result['returncode']} port={result['port']}", flush=True)

    results.sort(key=lambda item: next(i for i, row in enumerate(rows) if row["row_id"] == item["row_id"]))
    failed = [item["row_id"] for item in results if item["returncode"] != 0]
    append_jsonl(
        runner_events,
        {
            "event": "batch_finish",
            "timestamp": datetime.now(UTC).astimezone().isoformat(),
            "results": results,
            "failed_rows": failed,
        },
    )
    print(json.dumps({"results": results, "failed_rows": failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
