"""Tests for the PP-specific blind judge and clustered analysis."""

# ruff: noqa: C420, D101, D102, E501, F401, I001, PERF401, PT009

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.pp.fixed_boards.generate import build_set
from experiments.pp.fixed_boards.pp_judge import calibration_errors, evidence_errors, blind_unit
from experiments.pp.fixed_boards.score_analysis import content_cluster, paired_cluster_analysis, score_unit, scenario_scores


class PPFixedJudgeTest(unittest.TestCase):
    def test_blind_unit_hides_identifiers_and_names(self) -> None:
        inputs, labels = build_set("FB", analysis=True)
        item, label = inputs[0], labels[0]
        record = {
            "unit_id": f"{item['scenario_id']}__PPLUS__R1",
            "scenario_id": item["scenario_id"], "condition": "P+", "repetition": 1,
            "game_id": "G", "input_sha256": item["input_sha256"],
            "talk": "I vote Alice and ask Bob to join.", "vote": item["input"]["snapshot"]["players"][0],
        }
        blind, private = blind_unit(record, item, label, "seed")
        serialized = json.dumps(blind)
        self.assertNotIn("P+", serialized)
        self.assertNotIn(item["scenario_id"], serialized)
        self.assertEqual(private["condition"], "P+")

    def test_evidence_validation_rejects_fabricated_quote(self) -> None:
        judge_input = {
            "public_history": [],
            "target_talk": {"event_id": "target-talk", "text": "We have PP."},
            "target_vote": {"event_id": "target-vote", "target": "Player-3"},
        }
        value = {name: {"label": True, "evidence": [{"event_id": "target-talk", "quote": "not there"}], "reason": "x"}
                 for name in ("pp_recognized", "ally_recognized", "vote_coordination")}
        self.assertTrue(evidence_errors(value, judge_input))

    def test_evidence_validation_rejects_target_vote_id(self) -> None:
        judge_input = {
            "public_history": [],
            "target_talk": {"event_id": "target-talk", "text": "We have PP."},
            "target_vote": {"event_id": "target-vote", "target": "Player-3"},
        }
        value = {
            name: {
                "label": True,
                "evidence": [{"event_id": "target-vote", "quote": "Player-3"}],
                "reason": "x",
            }
            for name in ("pp_recognized", "ally_recognized", "vote_coordination")
        }
        errors = evidence_errors(value, judge_input)
        self.assertTrue(any("unknown event ID target-vote" in error for error in errors))

    def test_calibration_is_structurally_complete(self) -> None:
        self.assertEqual(calibration_errors(Path("experiments/pp/fixed_boards/judge/calibration.jsonl")), [])

    def test_content_cluster_joins_role_perspectives(self) -> None:
        self.assertEqual(content_cluster("FB-E-POS-001"), "FB-E-001")
        self.assertEqual(content_cluster("FB-E-WER-001"), "FB-E-001")

    def test_repeat_rule_and_paired_analysis(self) -> None:
        units = []
        for condition, success in (("P+", True), ("P-PP", False)):
            for role in ("POSSESSED", "WEREWOLF"):
                scenario = f"FB-E-{'POS' if role == 'POSSESSED' else 'WER'}-001"
                for repetition in (1, 2):
                    units.append({
                        "scenario_id": scenario, "content_cluster": "FB-E-001", "condition": condition,
                        "generation_repetition": repetition, "perspective_role": role,
                        "scenario_class": "established_pp", "components": {name: success for name in (
                            "pp_recognized", "correct_co_or_response", "ally_recognized", "ally_preserved",
                            "valid_target_selected", "coordinated_vote", "talk_vote_consistent")},
                        "composite_pp_success_repetition": success, "false_pp_repetition": None,
                    })
        scenarios = scenario_scores(units, expected_count=None)
        result = paired_cluster_analysis(scenarios, "composite_pp_success", bootstrap_iterations=20, permutation_iterations=100)
        self.assertEqual(result["paired_risk_difference"], 1.0)


if __name__ == "__main__":
    unittest.main()
