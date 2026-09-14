"""Tests for blind judge I/O and preregistered game-level analysis."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from experiments.evaluation.analyze import analyze, holm_adjust, wilson_interval
from experiments.evaluation.judge_inputs import anonymize_unit, select_stratified
from experiments.evaluation.judge_outputs import calibration_errors, validate_output
from experiments.evaluation.verify_saved_logs import parse_official_log


class JudgeInputTest(TestCase):
    """Ensure blinding removes treatment, outcome, names, and future events."""

    def test_anonymize_unit_hides_names_and_future(self) -> None:
        unit = {
            "unit_id": "game-1-talk-2",
            "game_id": "game-1",
            "condition": "G",
            "outcome": "WEREWOLF",
            "metric": "question_responsiveness",
            "target_agent": "Alice",
            "target_role": "VILLAGER",
            "cutoff_sequence": 2,
            "players": ["Alice", "Bob", "Carol"],
            "events": [
                {"sequence": 1, "event_id": "e1", "type": "talk", "speaker": "Bob", "text": "Alice, why Carol?"},
                {"sequence": 2, "event_id": "e2", "type": "talk", "speaker": "Alice", "text": "Carol changed votes."},
                {"sequence": 3, "event_id": "e3", "type": "result", "speaker": "server", "result": "WEREWOLF"},
                {"sequence": 1, "event_id": "secret", "type": "whisper", "speaker": "Carol", "public": False},
            ],
        }
        blind, private = anonymize_unit(unit)
        serialized = json.dumps(blind)
        self.assertNotIn("Alice", serialized)
        self.assertNotIn("Bob", serialized)
        self.assertNotIn("Carol", serialized)
        self.assertNotIn("game-1", serialized)
        self.assertNotIn("condition", serialized)
        self.assertNotIn("outcome", serialized)
        self.assertEqual([event["event_id"] for event in blind["public_history"]], ["e1", "e2"])
        self.assertNotIn("target_role", blind)
        self.assertEqual(private["condition"], "G")

    def test_role_is_disclosed_only_for_role_metric(self) -> None:
        unit = {
            "unit_id": "u",
            "metric": "role_action_quality",
            "target_agent": "Alice",
            "target_role": "SEER",
            "cutoff_sequence": 0,
            "events": [],
        }
        blind, _ = anonymize_unit(unit)
        self.assertEqual(blind["target_role"], "SEER")

    def test_stratified_sample_is_deterministic(self) -> None:
        records = [
            {
                "blind": {"blind_id": str(index), "metric": "vote_consistency"},
                "private": {"condition": "F" if index % 2 else "C", "role": "VILLAGER", "opponent_pool": "A"},
            }
            for index in range(12)
        ]
        first = select_stratified(records, 5, "seed")
        second = select_stratified(list(reversed(records)), 5, "seed")
        self.assertEqual([item["blind"]["blind_id"] for item in first], [item["blind"]["blind_id"] for item in second])
        self.assertEqual({item["private"]["condition"] for item in first}, {"F", "C"})


class JudgeOutputTest(TestCase):
    """Validate schema semantics and evidence faithfulness."""

    def test_valid_binary_output(self) -> None:
        judge_input = {"public_history": [{"event_id": "e1", "text": "I will vote for Player-2."}]}
        output = {
            "metric": "vote_consistency",
            "label": "pass",
            "score": 1,
            "rank": None,
            "evidence": [{"event_id": "e1", "quote": "vote for Player-2"}],
            "reason": "The targets match.",
        }
        self.assertEqual(validate_output(output, judge_input), [])

    def test_rejects_nonexistent_evidence(self) -> None:
        judge_input = {"public_history": [{"event_id": "e1", "text": "Player-2."}]}
        output = {
            "metric": "vote_consistency",
            "label": "fail",
            "score": 0,
            "rank": None,
            "evidence": [{"event_id": "e1", "quote": "Player-3"}],
            "reason": "Mismatch.",
        }
        self.assertTrue(any("not found" in error for error in validate_output(output, judge_input)))

    def test_calibration_covers_all_metrics(self) -> None:
        self.assertEqual(calibration_errors(Path("experiments/judge/calibration.jsonl")), [])


class AnalysisTest(TestCase):
    """Verify primary statistics on known inputs."""

    def test_wilson_known_tournament_result(self) -> None:
        interval = wilson_interval(52, 75)
        assert interval is not None
        self.assertAlmostEqual(interval[0], 0.5816954518)
        self.assertAlmostEqual(interval[1], 0.7861313291)

    def test_holm_is_monotone_in_sorted_order(self) -> None:
        adjusted = holm_adjust({"a": 0.01, "b": 0.03, "c": 0.04})
        self.assertEqual(adjusted, {"a": 0.03, "b": 0.06, "c": 0.06})

    def test_analysis_generates_condition_results(self) -> None:
        records = []
        for block in range(6):
            for condition, won in (("F", True), ("C", block % 2 == 0), ("R", False), ("G", block % 3 == 0)):
                records.append(
                    {
                        "game_id": f"{block}-{condition}",
                        "condition": condition,
                        "role": "VILLAGER" if block % 2 else "SEER",
                        "opponent_pool": "A" if block % 2 else "B",
                        "block": str(block),
                        "won": won,
                    }
                )
        result = analyze(records, bootstrap_iterations=100)
        self.assertEqual(result["included_games"], 24)
        self.assertEqual(len(result["primary_differences"]), 3)
        self.assertIsNotNone(result["logistic_regression"])

    def test_saved_log_parser_handles_unescaped_quotes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "123_team.log"
            path.write_text(
                "0,status,1,VILLAGER,ALIVE,yatolab1,Alice\n"
                '0,talk,0,0,1,An unmatched "quote,123\n'
                "1,result,1,0,VILLAGER\n",
                encoding="utf-8",
            )
            record = parse_official_log(path)
        assert record is not None
        self.assertTrue(record["won"])
