#!/usr/bin/env python3
"""Verify the analysis path against saved official five-player logs."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

if __package__:
    from .analyze import analyze
else:
    from analyze import analyze

VILLAGE_ROLES = {"VILLAGER", "SEER", "MEDIUM", "BODYGUARD"}
WOLF_ROLES = {"WEREWOLF", "POSSESSED"}


def parse_official_log(path: Path, team_prefix: str = "yatolab") -> dict[str, Any] | None:
    """Normalize one official CSV-like log for the requested team."""
    # The official logs are comma-delimited but do not CSV-escape quotation
    # marks in natural-language talk. Splitting physical lines preserves the
    # status/result records even when a talk line contains an unmatched quote.
    rows = [line.split(",", 6) for line in path.read_text(encoding="utf-8").splitlines()]
    statuses = [row for row in rows if len(row) >= 7 and row[0] == "0" and row[1] == "status"]
    targets = [row for row in statuses if row[5].startswith(team_prefix)]
    if len(targets) != 1:
        return None
    target = targets[0]
    role = target[3]
    results = [row for row in rows if len(row) >= 3 and row[1] == "result"]
    if not results:
        raise ValueError(f"No result in {path}")
    win_side = results[-1][-1].upper()
    if role in VILLAGE_ROLES:
        won = win_side in {"VILLAGER", "VILLAGE", "HUMAN"}
    elif role in WOLF_ROLES:
        won = win_side in {"WEREWOLF", "WOLF"}
    else:
        raise ValueError(f"Unknown role {role} in {path}")
    return {
        "game_id": path.name.split("_", 1)[0],
        "condition": "OFFICIAL",
        "role": role,
        "opponent_pool": "official_mixed",
        "block": path.name.split("_", 1)[0],
        "won": won,
        "excluded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--logs",
        type=Path,
        default=Path("doc/aiwolf-2026-INLG2-maintruck/log/MainTruck5"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    selected_paths = []
    records = []
    for path in sorted(args.logs.glob("*.log")):
        record = parse_official_log(path)
        if record is not None:
            selected_paths.append(path)
            records.append(record)
    result = analyze(records, bootstrap_iterations=100)
    total_wins = sum(int(record["won"]) for record in records)
    source_manifest = "".join(
        f"{path.name} {hashlib.sha256(path.read_bytes()).hexdigest()}\n" for path in selected_paths
    ).encode()
    verification = {
        "schema_version": 1,
        "verified_at": datetime.now(UTC).astimezone().isoformat(),
        "command": "uv run python3 experiments/evaluation/verify_saved_logs.py",
        "source": str(args.logs),
        "selected_log_count": len(records),
        "selected_log_sha256_manifest": hashlib.sha256(source_manifest).hexdigest(),
        "expected": {"games": 75, "wins": 52},
        "observed": {"games": len(records), "wins": total_wins},
        "wilson95": result["condition_summary"][0]["wilson95"],
        "passed": len(records) == 75 and total_wins == 52,
        "note": (
            "Natural-language talk lines are not CSV-escaped; the verifier parses status and result by physical line."
        ),
    }
    if args.output:
        args.output.write_text(
            json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "expected_games": verification["expected"]["games"],
                "expected_wins": verification["expected"]["wins"],
                "observed_games": verification["observed"]["games"],
                "observed_wins": verification["observed"]["wins"],
                "passed": verification["passed"],
            },
            sort_keys=True,
        ),
    )
    return 0 if verification["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
