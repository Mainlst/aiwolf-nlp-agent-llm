"""Tests for five-agent PP runtime usage accounting."""

# ruff: noqa: PT009

from __future__ import annotations

import csv
import contextlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import yaml

from experiments.evaluation.summarize import main


class RuntimeAccountingTest(TestCase):
    """Verify all five Luna clients join to one game cost record."""

    def test_five_agent_usage_is_joined_by_game_id(self) -> None:
        """Target and four opponent logs aggregate under the server game ID."""
        with TemporaryDirectory() as directory:
            run = Path(directory)
            plan = {
                "conditions": ["P+"],
                "target_team": "yatolab",
                "pricing_usd_per_million_tokens": {
                    "standard": {"short_context": {"input": 0.2, "cached_input": 0.02, "output": 1.2}},
                },
            }
            (run / "plan.yml").write_text(yaml.safe_dump(plan), encoding="utf-8")
            with (run / "assignments.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=("row_id", "condition", "planned_role"),
                )
                writer.writeheader()
                writer.writerow({"row_id": "PP01", "condition": "P+", "planned_role": "POSSESSED"})
            server_dir = run / "server-logs" / "PP01" / "json"
            server_dir.mkdir(parents=True)
            game = {
                "game_id": "game-1",
                "win_side": "WEREWOLF",
                "agents": [{"team": "yatolab", "role": "POSSESSED"}],
            }
            (server_dir / "game-1.json").write_text(json.dumps(game), encoding="utf-8")
            log_dirs = [run / "agent-logs" / "P+"]
            log_dirs.extend(run / "agent-logs" / "opponents" / f"opponentO{index}" for index in range(4))
            for index, log_dir in enumerate(log_dirs):
                log_dir.mkdir(parents=True)
                agent = "yatolab1" if index == 0 else f"opponentO{index}1"
                call = {
                    "game_id": "game-1",
                    "agent": agent,
                    "success": True,
                    "retry_count_observable": 0,
                    "model": {"model": "gpt-5.6-luna"},
                    "response_model_id": "gpt-5.6-luna",
                }
                usage = {"game_id": "game-1", "agent": agent, "usage": {"input_tokens": 1000, "output_tokens": 100}}
                (log_dir / "llm_calls.jsonl").write_text(json.dumps(call) + "\n", encoding="utf-8")
                (log_dir / "llm_usage.jsonl").write_text(json.dumps(usage) + "\n", encoding="utf-8")
            with (
                patch.object(sys, "argv", ["summarize.py", str(run)]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(), 0)
            summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
            record = summary["games"]["game-1"]
            self.assertEqual(len(record["agents"]), 5)
            self.assertEqual(record["calls"], 5)
            self.assertEqual(record["failed_calls"], 0)
            self.assertEqual(record["observable_retries"], 0)
            self.assertEqual(record["estimated_cost_usd"], 0.0016)
