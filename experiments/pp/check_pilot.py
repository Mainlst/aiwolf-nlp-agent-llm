#!/usr/bin/env python3
"""Static, API-free preflight for the P+/P-PP ten-game pilot."""

# ruff: noqa: C901, EM101, EM102, PLR0912, PLR0915, PLR2004, T201, TRY003

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from prepare_pilot import opponent_behavior_hashes, opponent_config, optimizer_seed

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "experiments" / "pp" / "pilot"


def load_mapping(path: Path) -> dict[str, Any]:
    """Load one YAML mapping."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected mapping: {path}")
    return value


def sha256(path: Path) -> str:
    """Return a file SHA-256 digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Validate condition configs, allocation balance, opponents, price, and backup target."""
    plan = load_mapping(PILOT / "plan.yml")
    registry = load_mapping(PILOT / "opponent-pools.yml")
    pp_manifest = json.loads((ROOT / "experiments" / "pp" / "generated" / "manifest.json").read_text(encoding="utf-8"))
    with (PILOT / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    if len(rows) != 10 or [int(row["run_order"]) for row in rows] != list(range(1, 11)):
        raise ValueError("Pilot allocation must contain run_order 1..10 exactly")
    conditions = Counter(row["condition"] for row in rows)
    if conditions != Counter({"P+": 5, "P-PP": 5}):
        raise ValueError(f"Bad condition allocation: {conditions}")
    if any(rows[index]["condition"] == rows[index + 1]["condition"] for index in range(9)):
        raise ValueError("Conditions must alternate to avoid run-order confounding")

    roles: dict[str, Counter[str]] = defaultdict(Counter)
    pools: dict[str, Counter[str]] = defaultdict(Counter)
    seats: dict[str, Counter[int]] = defaultdict(Counter)
    for row in rows:
        condition = row["condition"]
        roles[condition][row["planned_role"]] += 1
        pools[condition][row["opponent_pool"]] += 1
        seats[condition][int(row["target_seat"])] += 1
    expected_roles = {
        "P+": Counter({"POSSESSED": 3, "WEREWOLF": 2}),
        "P-PP": Counter({"WEREWOLF": 3, "POSSESSED": 2}),
    }
    expected_pools = {"P+": Counter({"O": 5}), "P-PP": Counter({"O": 5})}
    for condition in ("P+", "P-PP"):
        if roles[condition] != expected_roles[condition]:
            raise ValueError(f"Bad role allocation for {condition}: {roles[condition]}")
        if pools[condition] != expected_pools[condition]:
            raise ValueError(f"Bad pool allocation for {condition}: {pools[condition]}")
        if seats[condition] != Counter({1: 1, 2: 1, 3: 1, 4: 1, 5: 1}):
            raise ValueError(f"Bad seat allocation for {condition}: {seats[condition]}")
    for row in rows:
        seed = optimizer_seed(row, registry)
        target_index = int(row["target_seat"]) - 1
        if seed["idx_team_map"][str(target_index)] != plan["target_team"]:
            raise ValueError(f"Target seat is not enforced for {row['row_id']}")
        if seed["scheduled_matches"][0]["role_idxs"][row["planned_role"]] != [target_index]:
            raise ValueError(f"Target role is not enforced for {row['row_id']}")

    generated_dir = ROOT / "experiments" / "pp" / "generated"
    for condition in ("P+", "P-PP"):
        item = pp_manifest["configs"][condition]
        if sha256(generated_dir / item["file"]) != item["sha256"]:
            raise ValueError(f"Generated condition hash mismatch: {condition}")
        generated = load_mapping(generated_dir / item["file"])
        if generated["openai"]["model"] != "gpt-5.6-luna":
            raise ValueError(f"Generated target condition is not Luna: {condition}")

    source_file = ROOT / registry["source_file"]
    qualification = ROOT / registry["qualification_report"]
    if sha256(source_file) != registry["source_file_sha256"]:
        raise ValueError("Opponent source hash changed after registry freeze")
    if sha256(qualification) != registry["qualification_report_sha256"]:
        raise ValueError("Opponent qualification hash changed after registry freeze")
    qualification_data = json.loads(qualification.read_text(encoding="utf-8"))
    if not all(qualification_data["pools"][pool]["passed"] for pool in ("A", "B", "C")):
        raise ValueError("At least one opponent pool lacks passing qualification evidence")
    for raw_log in registry["qualification_raw_logs"]:
        raw_path = ROOT / raw_log["file"]
        if not raw_path.is_file() or sha256(raw_path) != raw_log["sha256"]:
            raise ValueError(f"Stage 0 raw qualification log mismatch: {raw_log['file']}")
    teams = [team for pool in registry["pools"].values() for team in pool["agents"]]
    if len(teams) != 12 or len(set(teams)) != 12:
        raise ValueError("Opponent registry must contain twelve distinct teams")

    expected_price = {
        "source": "https://developers.openai.com/api/docs/pricing",
        "retrieved_at": "2026-08-15",
        "context_threshold_tokens": 272000,
        "standard": {
            "short_context": {"input": 0.2, "cached_input": 0.02, "cache_write": 0.25, "output": 1.2},
            "long_context": {"input": 0.4, "cached_input": 0.04, "cache_write": 0.5, "output": 1.8},
        },
        "batch": {
            "short_context": {"input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.6},
            "long_context": {"input": 0.2, "cached_input": 0.02, "cache_write": 0.25, "output": 0.9},
        },
        "note": "Prices are USD per 1M tokens; prompts over 272K input tokens use long-context rates.",
    }
    model = plan["fixed_runtime"]["model"]
    if model != "gpt-5.6-luna" or plan["pricing_usd_per_million_tokens"] != expected_price:
        raise ValueError("Frozen model or official pricing record changed")
    runtime_opponent = registry["runtime_opponent"]
    if runtime_opponent != {
        "id": "O",
        "uses_paid_api": True,
        "condition_independent": True,
        "model": "gpt-5.6-luna",
        "temperature": 1.0,
        "max_retries": 0,
        "agents": ["opponentOalpha", "opponentObravo", "opponentOcharlie", "opponentOdelta"],
        "speech_styles": ["fixed-neutral"],
    }:
        raise ValueError("Runtime opponent O is not the frozen Luna configuration")
    audit_run = ROOT / "experiments" / "runs" / "<audit>"
    o_hashes = [opponent_behavior_hashes(opponent_config(team, plan, audit_run)) for team in runtime_opponent["agents"]]
    if any(item != o_hashes[0] for item in o_hashes[1:]):
        raise ValueError("Opponent O aliases differ in functional behavior")
    if any("condition" in opponent_config(team, plan, audit_run)["experiment"] for team in runtime_opponent["agents"]):
        raise ValueError("Opponent O must not receive a target condition ID")
    backup_destination = Path(plan["backup"]["destination"])
    if not backup_destination.is_dir():
        raise FileNotFoundError(f"Backup destination does not exist: {backup_destination}")

    print("OK: P+/P-PP=5/5, roles and seats balanced, fixed Luna opponent O and hashes verified")
    if plan["budget"]["remaining_usd"] is None:
        if plan["budget"]["remaining_budget_status"] != "unavailable_user_authorized_skip":
            raise ValueError("Missing remaining budget without an explicit user-authorized skip")
        print("SKIPPED: remaining budget unavailable; user authorized proceeding without the dashboard value")
    estimate_path = ROOT / plan["budget"]["estimate_source"]
    estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
    expected_budget_values = {
        "required_generation_estimate_usd": estimate["estimates"]["required_generation_standard"],
        "all_in_estimate_with_optional_and_judge_reserve_usd": estimate["estimates"][
            "all_in_expected_with_optional_and_judge_reserve"
        ],
        "recommended_operational_reserve_usd": estimate["estimates"]["recommended_operational_reserve"],
    }
    if any(plan["budget"][key] != value for key, value in expected_budget_values.items()):
        raise ValueError("Pilot budget record differs from the measured budget estimate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
