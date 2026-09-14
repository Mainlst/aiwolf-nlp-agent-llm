#!/usr/bin/env python3
"""Verify API-free opponent pools from completed Stage 0 logs."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_ROLES = Counter({"Role.VILLAGER": 2, "Role.SEER": 1, "Role.POSSESSED": 1, "Role.WEREWOLF": 1})


def jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL objects."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def sha256(path: Path) -> str:
    """Return a source log digest for later audit."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Check completion, role coverage, and interaction opportunities for A/B/C."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    report: dict[str, Any] = {"run_dir": str(run_dir), "pools": {}, "passed": True}

    for pool in ("A", "B", "C"):
        opponent_log = run_dir / f"stage0-{pool}-opponents.jsonl"
        rows = jsonl(opponent_log)
        talks = [str(row.get("response", "")) for row in rows if row.get("request") == "TALK"]
        finish_rows = [row for row in rows if row.get("request") == "FINISH"]
        roles = Counter(str(row.get("role")) for row in finish_rows)
        json_games = list((run_dir / "server-logs" / f"stage0-{pool}" / "json").glob("*.json"))
        checks = {
            "one_server_game": len(json_games) == 1,
            "five_distinct_teams": len({row.get("original_name") for row in rows if row.get("request") == "NAME"}) == 5,
            "all_clients_finished": len(finish_rows) == 5,
            "balanced_roles": roles == EXPECTED_ROLES,
            "valid_talk": any(talk and talk != "Skip" for talk in talks),
            "question_present": any("?" in talk for talk in talks),
            "seer_claim_present": any("I am the seer" in talk for talk in talks),
            "suspicion_present": any("suspect" in talk.lower() for talk in talks),
            "vote_declaration_present": any("vote" in talk.lower() for talk in talks),
            "vote_action_present": any(row.get("request") == "VOTE" for row in rows),
        }
        passed = all(checks.values())
        report["pools"][pool] = {
            "passed": passed,
            "checks": checks,
            "roles": dict(roles),
            "talk_count": len(talks),
            "source_log": str(opponent_log),
            "source_log_sha256": sha256(opponent_log),
            "server_game": str(json_games[0]) if len(json_games) == 1 else None,
            "server_game_sha256": sha256(json_games[0]) if len(json_games) == 1 else None,
        }
        report["passed"] = report["passed"] and passed

    output = run_dir / "qualification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
