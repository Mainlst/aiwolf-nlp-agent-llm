"""Tests for the P+/P-PP pilot allocation and optimizer seeds."""

# ruff: noqa: PT009, PT027

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import yaml

from experiments.pp.prepare_pilot import (
    opponent_behavior_hashes,
    opponent_config,
    optimizer_seed,
    validate_server_binary,
)
from experiments.runtime.run_one import validate_run_server_binary


class PPPilotPreparationTest(TestCase):
    """Check that planned roles become explicit server optimizer inputs."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the frozen pilot inputs."""
        pilot = Path("experiments/pp/pilot")
        cls.registry = yaml.safe_load((pilot / "opponent-pools.yml").read_text(encoding="utf-8"))
        with (pilot / "assignments.csv").open(encoding="utf-8", newline="") as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_each_seed_forces_the_target_role(self) -> None:
        """Every row assigns yatolab's planned seat to its planned wolf-side role."""
        for row in self.rows:
            seed = optimizer_seed(row, self.registry)
            target_index = int(row["target_seat"]) - 1
            self.assertEqual(seed["idx_team_map"][str(target_index)], "yatolab")
            self.assertEqual(seed["scheduled_matches"][0]["role_idxs"][row["planned_role"]], [target_index])

    def test_each_seed_contains_exactly_five_distinct_teams(self) -> None:
        """A row combines the target with exactly four agents from its selected pool."""
        for row in self.rows:
            seed = optimizer_seed(row, self.registry)
            teams = list(seed["idx_team_map"].values())
            self.assertEqual(len(teams), 5)
            self.assertEqual(len(set(teams)), 5)

    def test_each_condition_uses_every_target_seat_once(self) -> None:
        """The optimizer input, not only the CSV label, rotates the target seat."""
        for condition in ("P+", "P-PP"):
            seats = []
            for row in self.rows:
                if row["condition"] != condition:
                    continue
                seed = optimizer_seed(row, self.registry)
                seats.append(next(int(index) + 1 for index, team in seed["idx_team_map"].items() if team == "yatolab"))
            self.assertEqual(sorted(seats), [1, 2, 3, 4, 5])

    def test_opponent_o_is_condition_independent_and_functionally_identical(self) -> None:
        """All four O aliases share one Luna behavior and receive no condition ID."""
        plan = yaml.safe_load((Path("experiments/pp/pilot") / "plan.yml").read_text(encoding="utf-8"))
        run_dir = Path("experiments/runs/test-audit")
        configs = [opponent_config(team, plan, run_dir) for team in plan["opponent_O"]["agents"]]
        hashes = [opponent_behavior_hashes(config) for config in configs]
        self.assertTrue(all(item == hashes[0] for item in hashes))
        self.assertTrue(all(config["openai"]["model"] == "gpt-5.6-luna" for config in configs))
        self.assertTrue(all(config["openai"]["temperature"] == 1.0 for config in configs))
        self.assertTrue(all("condition" not in config["experiment"] for config in configs))

    def test_plan_pinned_server_binary_is_enforced(self) -> None:
        """A plan-pinned binary hash rejects a different server executable."""
        with TemporaryDirectory() as directory:
            binary = Path(directory) / "server"
            binary.write_bytes(b"wrong artifact")
            with self.assertRaises(ValueError):
                validate_server_binary({"required_server_binary_sha256": "0" * 64}, binary)

    def test_plan_without_pinned_server_binary_remains_supported(self) -> None:
        """Existing plans without a pinned binary hash remain compatible."""
        validate_server_binary({}, Path("tests/fixtures/nonexistent-server-binary"))

    def test_unpinned_format_run_is_retired(self) -> None:
        """A legacy format run cannot launch without a plan-pinned binary."""
        with self.assertRaises(ValueError):
            validate_run_server_binary({"formats": ["turn_based", "anytime"]}, {"server": {}})

    def test_format_run_manifest_must_match_plan_pin(self) -> None:
        """The frozen manifest and format plan must name the same binary hash."""
        plan = {"formats": ["turn_based", "anytime"], "required_server_binary_sha256": "a" * 64}
        manifest = {"server": {"binary_sha256": "b" * 64}}
        with self.assertRaises(ValueError):
            validate_run_server_binary(plan, manifest)
