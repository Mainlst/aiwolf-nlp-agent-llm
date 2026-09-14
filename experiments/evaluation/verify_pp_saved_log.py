#!/usr/bin/env python3
"""Regression-check PP extraction against one frozen official game log."""

# ruff: noqa: PLR2004, T201

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

if __package__:
    from .pp_log_parser import parse_game_log
else:
    from pp_log_parser import parse_game_log

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = (
    ROOT
    / "doc"
    / "aiwolf-2026-INLG2-maintruck"
    / "log"
    / "MainTruck5"
    / "1786006401_AGKAI_Luna_Mackerel_Oxymoron_yatolab.log"
)
DEFAULT_OUTPUT = ROOT / "experiments" / "validation" / "pp-saved-log-verification.json"


def main() -> int:
    """Check the expected PP board, COs, targets, coordination, and joins."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    parsed = parse_game_log(args.log)
    board = next((item for item in parsed["pp_boards"] if item["day"] == 2), None)
    cos = {(item["day"], item["agent"], item["claimed_role"]) for item in parsed["role_cos"]}
    joins = {(item["day"], item["agent"]): item for item in parsed["talk_vote_joins"]}
    checks = {
        "game_id": parsed["game_id"] == "1786006401",
        "win_side": parsed["win_side"] == "WEREWOLF",
        "roles": parsed["roles"].get(1) == "POSSESSED" and parsed["roles"].get(5) == "WEREWOLF",
        "three_survivor_pp": board is not None and board["alive"] == [1, 3, 5] and board["village_target"] == 3,
        "possessed_co": (2, 1, "POSSESSED") in cos,
        "werewolf_co": (2, 5, "WEREWOLF") in cos,
        "actual_votes": parsed["votes"].get(2) == {1: 3, 3: 5, 5: 3},
        "vote_coordination": board is not None and board["vote_coordinated"] is True,
        "possessed_talk_vote_join": joins.get((2, 1), {}).get("declared_target") == 3
        and joins.get((2, 1), {}).get("matched") is True,
        "werewolf_talk_vote_join": joins.get((2, 5), {}).get("declared_target") == 3
        and joins.get((2, 5), {}).get("matched") is True,
    }
    report = {
        "schema_version": 1,
        "source": str(args.log.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(args.log.read_bytes()).hexdigest(),
        "expected_fixture": {
            "day": 2,
            "alive": [1, 3, 5],
            "possessed": 1,
            "werewolf": 5,
            "village_target": 3,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
