"""Tests for blinded PP normal-game block auditing."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import yaml

from experiments.pp.v2.normal.audit_block import main


class NormalBlockAuditTest(TestCase):
    """Ensure seat and model drift stop a parallel block."""

    def make_run(self, root: Path, wrong_seat: bool = False) -> Path:
        """Create one complete synthetic 20-game block."""
        run = root / "run"
        run.mkdir()
        teams = ["opponentOalpha", "opponentObravo", "opponentOcharlie", "opponentOdelta"]
        plan = {
            "target_team": "yatolab",
            "opponent_O": {"agents": teams},
            "fixed_runtime": {"model": "gpt-5.6-luna"},
            "pricing_usd_per_million_tokens": {
                "standard": {"short_context": {"input": 0.2, "cached_input": 0.02, "output": 1.2}},
            },
        }
        (run / "plan.yml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        frozen_files: dict[str, str] = {}
        for condition in ("P+", "P-PP"):
            path = run / "agent-configs" / f"{condition}.yml"
            path.parent.mkdir(exist_ok=True)
            path.write_text(yaml.safe_dump({"condition": condition}), encoding="utf-8")
            frozen_files[str(path.relative_to(run))] = hashlib.sha256(path.read_bytes()).hexdigest()
        for team in teams:
            path = run / "opponent-configs" / f"{team}.yml"
            path.parent.mkdir(exist_ok=True)
            path.write_text(yaml.safe_dump({"team": team}), encoding="utf-8")
            frozen_files[str(path.relative_to(run))] = hashlib.sha256(path.read_bytes()).hexdigest()
        fields = ("row_id", "condition", "analysis_block", "planned_role", "target_seat")
        rows = []
        with (run / "assignments.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for index in range(20):
                condition = "P+" if index < 10 else "P-PP"
                row = {
                    "row_id": f"row-{index:02d}",
                    "condition": condition,
                    "analysis_block": "01",
                    "planned_role": "POSSESSED",
                    "target_seat": str(index % 5 + 1),
                }
                rows.append(row)
                writer.writerow(row)
        finish_events = []
        for index, row in enumerate(rows):
            game_id = f"game-{index:02d}"
            actual_seat = int(row["target_seat"])
            if wrong_seat and index == 0:
                actual_seat = actual_seat % 5 + 1
            target_index = int(row["target_seat"]) - 1
            ordered_teams = list(teams)
            ordered_teams.insert(target_index, "yatolab")
            other_indexes = [seat for seat in range(5) if seat != target_index]
            role_indexes = {
                "POSSESSED": [target_index],
                "WEREWOLF": [other_indexes[0]],
                "SEER": [other_indexes[1]],
                "VILLAGER": other_indexes[2:],
            }
            optimizer = {
                "idx_team_map": {str(seat): team for seat, team in enumerate(ordered_teams)},
                "scheduled_matches": [{"role_idxs": role_indexes, "weight": 1.0}],
            }
            seed_path = run / "optimizer-seeds" / f"{row['row_id']}.json"
            seed_path.parent.mkdir(exist_ok=True)
            seed_path.write_text(json.dumps(optimizer) + "\n", encoding="utf-8")
            frozen_files[str(seed_path.relative_to(run))] = hashlib.sha256(seed_path.read_bytes()).hexdigest()
            role_by_index = {seat: role for role, indexes in role_indexes.items() for seat in indexes}
            roster = [
                {"idx": seat + 1, "team": team, "role": role_by_index[seat]}
                for seat, team in enumerate(ordered_teams)
            ]
            if wrong_seat and index == 0:
                target_agent = next(agent for agent in roster if agent["team"] == "yatolab")
                swap_agent = next(agent for agent in roster if agent["idx"] == actual_seat)
                target_agent["idx"], swap_agent["idx"] = swap_agent["idx"], target_agent["idx"]
            finish_events.append(
                {
                    "event": "finish",
                    "row_id": row["row_id"],
                    "attempt_id": "attempt-1",
                    "game_id": game_id,
                    "game_log_count": 1,
                    "actual_role": "POSSESSED",
                    "actual_seat": actual_seat,
                    "roster": roster,
                    "timed_out": False,
                    "agent_returncode": 0,
                    "opponent_returncodes": [0, 0, 0, 0],
                    "server_returncode_after_termination": 0,
                    "client_finish_confirmed": [True, True, True, True, True],
                },
            )
            log_dirs = [run / "agent-logs" / row["condition"] / row["row_id"] / "attempt-1"]
            log_dirs.extend(
                run / "agent-logs" / "opponents" / team / row["row_id"] / "attempt-1"
                for team in teams
            )
            for agent_index, log_dir in enumerate(log_dirs):
                log_dir.mkdir(parents=True)
                common = {
                    "game_id": game_id,
                    "agent": f"agent-{agent_index}",
                    "model": {"model": "gpt-5.6-luna"},
                    "response_model_id": None,
                }
                (log_dir / "llm_calls.jsonl").write_text(json.dumps(common) + "\n", encoding="utf-8")
                usage = {**common, "usage": {"input_tokens": 100, "output_tokens": 10}}
                (log_dir / "llm_usage.jsonl").write_text(json.dumps(usage) + "\n", encoding="utf-8")
        (run / "execution.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in finish_events),
            encoding="utf-8",
        )
        (run / "manifest.json").write_text(json.dumps({"files": frozen_files}) + "\n", encoding="utf-8")
        return run

    def test_complete_parallel_block_passes(self) -> None:
        """All 20 games pass without exposing condition outcomes."""
        with TemporaryDirectory() as directory:
            run = self.make_run(Path(directory))
            with (
                patch.object(sys, "argv", ["audit_block.py", str(run), "01"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(), 0)
            report = json.loads((run / "block-audits" / "block-01.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertFalse(report["condition_specific_outcomes_included"])
            self.assertEqual(report["eligible_games"], 20)

    def test_actual_seat_mismatch_fails_block(self) -> None:
        """A single runtime seat mismatch prevents continuation."""
        with TemporaryDirectory() as directory:
            run = self.make_run(Path(directory), wrong_seat=True)
            with (
                patch.object(sys, "argv", ["audit_block.py", str(run), "01"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(), 1)
            report = json.loads((run / "block-audits" / "block-01.json").read_text(encoding="utf-8"))
            self.assertIn("row-00: target_seat_mismatch", report["failures"])
