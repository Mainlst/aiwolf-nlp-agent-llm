#!/usr/bin/env python3
"""Static preflight checks for the 20-game pilot; never calls an API."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "experiments" / "pilot"
EXPECTED_ROLES = Counter({"VILLAGER": 2, "SEER": 1, "POSSESSED": 1, "WEREWOLF": 1})


def load_yaml(path: Path) -> dict[str, Any]:
    """Load one mapping from YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping: {path}")
    return data


def main() -> int:
    """Validate the preregistered allocation and condition switches."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="Also require completed opponent-pool records")
    args = parser.parse_args()

    plan = load_yaml(PILOT / "plan.yml")
    conditions = plan["conditions"]
    expected = {
        "F": (True, True, True, "sanitize"),
        "C": (False, True, True, "sanitize"),
        "R": (True, False, True, "sanitize"),
        "G": (True, True, False, "raw"),
    }
    for condition, values in expected.items():
        item = conditions[condition]
        actual = (
            item["dynamic_context_enabled"],
            item["role_strategy_enabled"],
            item["output_guard_enabled"],
            item["output_guard_disabled_behavior"],
        )
        if actual != values:
            raise ValueError(f"Condition {condition} is not the preregistered ablation: {actual}")

    with (PILOT / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 20:
        raise ValueError(f"Expected 20 rows, got {len(rows)}")
    if [int(row["run_order"]) for row in rows] != list(range(1, 21)):
        raise ValueError("run_order must be exactly 1..20")

    per_condition: dict[str, Counter[str]] = defaultdict(Counter)
    pools: Counter[str] = Counter()
    for row in rows:
        per_condition[row["condition"]][row["planned_role"]] += 1
        pools[row["opponent_pool"]] += 1
    for condition in expected:
        if per_condition[condition] != EXPECTED_ROLES:
            raise ValueError(f"Bad role allocation for {condition}: {per_condition[condition]}")
    if pools != Counter({"A": 7, "B": 7, "C": 6}):
        raise ValueError(f"Bad pool allocation: {pools}")

    opponent_config = load_yaml(PILOT / "opponent-pools.yml")
    pools_config = opponent_config["pools"]
    pool_problems = []
    all_teams = []
    for pool_id in ("A", "B", "C"):
        pool = pools_config[pool_id]
        agents = pool.get("agents", [])
        teams = [agent.get("team") for agent in agents]
        all_teams.extend(teams)
        if len(agents) != 4 or len(set(teams)) != 4:
            pool_problems.append(f"pool {pool_id} must contain four distinct teams")
        if any(agent.get("uses_paid_api") is not False for agent in agents):
            pool_problems.append(f"pool {pool_id} must be API-free")
        qualification = str(pool.get("qualification_log", ""))
        qualification_path = ROOT / qualification
        if qualification == "PENDING" or not qualification_path.is_file():
            pool_problems.append(f"pool {pool_id} has no saved qualification log")
        else:
            qualification_data = json.loads(qualification_path.read_text(encoding="utf-8"))
            if not qualification_data.get("pools", {}).get(pool_id, {}).get("passed", False):
                pool_problems.append(f"pool {pool_id} did not pass Stage 0 qualification")
    if len(set(all_teams)) != 12:
        pool_problems.append("all twelve opponent team names must be distinct")
    if args.strict and pool_problems:
        raise ValueError("; ".join(pool_problems))
    print("OK: conditions=4, games=20, roles per condition=2/1/1/1, pools=A7/B7/C6")
    if pool_problems:
        print("WARNING: " + "; ".join(pool_problems))
        print("Run API-free Stage 0 matches, save their logs, then run --strict before any paid call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
