#!/usr/bin/env python3
"""Estimate the complete experiment budget from a measured Luna game."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONNECTION = ROOT / "experiments" / "validation" / "pp-luna-connection.json"
DEFAULT_OUTPUT = ROOT / "experiments" / "validation" / "experiment-budget-estimate.json"


def round_usd(value: float) -> float:
    """Round a USD estimate to six decimal places."""
    return round(value, 6)


def main() -> int:
    """Calculate planned, optional, judge-reserve, and contingency totals."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", type=Path, default=DEFAULT_CONNECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    connection: dict[str, Any] = json.loads(args.connection.read_text(encoding="utf-8"))
    totals = connection["totals"]
    observed_game_cost = float(totals["estimated_cost_usd"])
    observed_calls = int(totals["calls"])
    observed_call_cost = observed_game_cost / observed_calls

    counts = {
        "pilot_games": 10,
        "normal_games": 400,
        "format_comparison_games": 60,
        "fixed_board_calls": 960,
        "optional_role_candidate_calls": 240,
    }
    live_game_count = counts["pilot_games"] + counts["normal_games"] + counts["format_comparison_games"]
    live_games_cost = live_game_count * observed_game_cost
    fixed_standard_cost = counts["fixed_board_calls"] * observed_call_cost
    fixed_batch_cost = fixed_standard_cost / 2
    optional_standard_cost = counts["optional_role_candidate_calls"] * observed_call_cost
    optional_batch_cost = optional_standard_cost / 2
    required_generation_standard = live_games_cost + fixed_standard_cost
    judge_reserve = 30.0
    already_spent = observed_game_cost
    all_in_expected = required_generation_standard + judge_reserve + optional_standard_cost + already_spent
    contingency_multiplier = 1.25
    all_in_with_contingency = all_in_expected * contingency_multiplier
    recommended_reserve = math.ceil(all_in_with_contingency / 5) * 5

    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "currency": "USD",
        "basis": {
            "run_id": connection["run_id"],
            "game_id": connection["game_id"],
            "observed_game_cost": round_usd(observed_game_cost),
            "observed_calls_per_game": observed_calls,
            "observed_mean_cost_per_call": round_usd(observed_call_cost),
            "sample_games": 1,
            "confidence": "provisional; replace with blinded median and p90 after the 10-game pilot",
        },
        "planned_counts": counts,
        "estimates": {
            "live_games_470_standard": round_usd(live_games_cost),
            "fixed_boards_960_standard": round_usd(fixed_standard_cost),
            "fixed_boards_960_batch": round_usd(fixed_batch_cost),
            "required_generation_standard": round_usd(required_generation_standard),
            "optional_role_candidates_240_standard": round_usd(optional_standard_cost),
            "optional_role_candidates_240_batch": round_usd(optional_batch_cost),
            "fixed_judge_reserve": judge_reserve,
            "connection_check_already_spent": round_usd(already_spent),
            "all_in_expected_with_optional_and_judge_reserve": round_usd(all_in_expected),
            "contingency_multiplier": contingency_multiplier,
            "all_in_with_contingency": round_usd(all_in_with_contingency),
            "recommended_operational_reserve": float(recommended_reserve),
            "preregistered_absolute_cap": 300.0,
        },
        "assumptions": [
            "All 470 live games cost the same as the single measured five-Luna connection game.",
            "Fixed-board and optional calls cost the measured mean per call; Batch is modeled at 50% of Standard.",
            "The fixed semantic judge uses a separate conservative reserve of USD 30 from its sampling policy.",
            "Retries are not added because the measured run had zero failures and zero observable retries.",
            "The USD 55 recommendation includes the optional 240 calls and a 25% contingency.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"required_generation_usd={required_generation_standard:.6f} "
        f"all_in_expected_usd={all_in_expected:.6f} recommended_reserve_usd={recommended_reserve}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
