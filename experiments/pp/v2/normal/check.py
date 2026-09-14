#!/usr/bin/env python3
"""API-free fail-closed audit of the PP v2 400-game allocation."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
CONDITIONS = ROOT / "experiments" / "pp" / "v2" / "generated"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    plan = yaml.safe_load((HERE / "plan.yml").read_text(encoding="utf-8"))
    with (HERE / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 400 or [int(row["run_order"]) for row in rows] != list(range(1, 401)):
        raise ValueError("allocation must contain exactly run_order 1..400")
    if Counter(row["condition"] for row in rows) != Counter({"P+": 200, "P-PP": 200}):
        raise ValueError("condition allocation is not 200/200")
    pairs: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        pairs[row["match_pair_id"]].append(row)
    if len(pairs) != 200:
        raise ValueError("expected 200 match pairs")
    matched = ("planned_role", "target_seat", "opponent_pool", "analysis_block", "optimizer_seed")
    for pair_id, pair in pairs.items():
        if len(pair) != 2 or {row["condition"] for row in pair} != {"P+", "P-PP"}:
            raise ValueError(f"{pair_id}: incomplete condition pair")
        if any(len({row[field] for row in pair}) != 1 for field in matched):
            raise ValueError(f"{pair_id}: matched setting differs")
    for condition in ("P+", "P-PP"):
        subset = [row for row in rows if row["condition"] == condition]
        if Counter(row["planned_role"] for row in subset) != Counter({"POSSESSED": 100, "WEREWOLF": 100}):
            raise ValueError(f"{condition}: role imbalance")
        if Counter(row["target_seat"] for row in subset) != Counter({str(i): 40 for i in range(1, 6)}):
            raise ValueError(f"{condition}: seat imbalance")
        if Counter(row["analysis_block"] for row in subset) != Counter({f"{i:02d}": 10 for i in range(1, 21)}):
            raise ValueError(f"{condition}: block imbalance")
    manifest = json.loads((CONDITIONS / "manifest.json").read_text(encoding="utf-8"))
    for condition, metadata in manifest["configs"].items():
        config = yaml.safe_load((CONDITIONS / metadata["file"]).read_text(encoding="utf-8"))
        if sha256(CONDITIONS / metadata["file"]) != metadata["sha256"]:
            raise ValueError(f"{condition}: condition hash mismatch")
        if config["experiment"]["intervention_version"] != "v2-conflict-resolved":
            raise ValueError(f"{condition}: not the v2 intervention")
        if config["openai"]["model"] != "gpt-5.6-luna" or config["openai"]["temperature"] != 1.0:
            raise ValueError(f"{condition}: runtime mismatch")
    if plan["matched_pairs"] != 200 or plan["assignment_seed"] != 20260817:
        raise ValueError("normal-game plan drifted")
    print("OK: 400 games, 200 matched pairs, conditions/roles/seats/blocks balanced, v2 hashes valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
