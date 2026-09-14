#!/usr/bin/env python3
"""Generate the deterministic 200-pair PP v2 normal-game allocation."""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "assignments.csv"
FIELDS = (
    "row_id", "run_order", "condition", "planned_role", "target_seat", "opponent_pool",
    "analysis_block", "match_pair_id", "optimizer_seed", "status", "game_id", "actual_role", "notes",
)


def build(seed: int = 20260817) -> list[dict[str, str]]:
    """Return 400 rows in 20 randomized complete blocks."""
    rng = random.Random(seed)
    ordered: list[dict[str, str]] = []
    for block in range(1, 21):
        block_rows = []
        for within in range(1, 11):
            pair = (block - 1) * 10 + within
            role = "POSSESSED" if pair % 2 else "WEREWOLF"
            seat = (pair - 1) % 5 + 1
            pair_id = f"V2P{pair:03d}"
            optimizer = str(202608170000 + pair)
            for condition in ("P+", "P-PP"):
                block_rows.append({
                    "row_id": f"{pair_id}-{'PLUS' if condition == 'P+' else 'MINUS'}",
                    "run_order": "",
                    "condition": condition,
                    "planned_role": role,
                    "target_seat": str(seat),
                    "opponent_pool": "O",
                    "analysis_block": f"{block:02d}",
                    "match_pair_id": pair_id,
                    "optimizer_seed": optimizer,
                    "status": "planned",
                    "game_id": "",
                    "actual_role": "",
                    "notes": "",
                })
        rng.shuffle(block_rows)
        ordered.extend(block_rows)
    for run_order, row in enumerate(ordered, 1):
        row["run_order"] = str(run_order)
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=20260817)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(build(args.seed))
    print(f"generated=400 matched_pairs=200 output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
