"""Tests for deterministic PP fixed-board generation and scoring."""

# ruff: noqa: PT009

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from unittest import TestCase

from experiments.pp.fixed_boards.check import audit_pair
from experiments.pp.fixed_boards.generate import build_set
from experiments.pp.fixed_boards.score_confirmation import score_record
from experiments.pp.fixed_boards_v2.generate import DEFAULT_SPEC as V2_SPEC_PATH
from experiments.pp.fixed_boards_v2.generate import build_set_v2
import yaml


class PPFixedBoardTest(TestCase):
    """Verify corpus balance, determinism, isolation, and scorer behavior."""

    def test_analysis_corpus_is_deterministic_balanced_and_unique(self) -> None:
        """The generator always returns the preregistered 120 distinct inputs."""
        first_inputs, first_labels = build_set("FB", analysis=True)
        second_inputs, second_labels = build_set("FB", analysis=True)
        self.assertEqual(first_inputs, second_inputs)
        self.assertEqual(first_labels, second_labels)
        self.assertEqual(len(first_inputs), 120)
        self.assertEqual(len({item["input_sha256"] for item in first_inputs}), 120)
        counts = {
            (scenario_class, role): sum(
                label["scenario_class"] == scenario_class and label["perspective_role"] == role
                for label in first_labels
            )
            for scenario_class in ("established_pp", "near_control")
            for role in ("POSSESSED", "WEREWOLF")
        }
        self.assertEqual(
            counts,
            {
                ("established_pp", "POSSESSED"): 40,
                ("established_pp", "WEREWOLF"): 40,
                ("near_control", "POSSESSED"): 20,
                ("near_control", "WEREWOLF"): 20,
            },
        )
        family_counts = Counter(
            (label["scenario_class"], label["perspective_role"], label["stratum"], label["template_family"])
            for label in first_labels
        )
        self.assertLessEqual(max(family_counts.values()), 4)

    def test_inputs_pass_future_and_ground_truth_audit(self) -> None:
        """Labels derive from hidden roles while model inputs contain no label keys."""
        inputs, labels = build_set("FB", analysis=True)
        forbidden = {
            "winner",
            "win_side",
            "future_events",
            "future_vote",
            "ground_truth_roles",
            "hidden_roles",
            "correct_ally",
            "valid_village_targets",
            "objective_pp_possible",
            "epistemic_pp_justified",
        }
        self.assertEqual(
            [error for item, label in zip(inputs, labels, strict=True) for error in audit_pair(item, label, forbidden)],
            [],
        )

    def test_near_controls_do_not_reveal_the_answer(self) -> None:
        """Control dialogue supplies ambiguous cues rather than its ground truth."""
        inputs, labels = build_set("FB", analysis=True)
        banned = ("premature", "nobody should reveal", "unreliable", "not established", "not pp")
        for item, label in zip(inputs, labels, strict=True):
            if label["scenario_class"] != "near_control":
                continue
            text = " ".join(event.get("text", "") for event in item["input"]["snapshot"]["public_history"]).casefold()
            self.assertFalse(any(phrase in text for phrase in banned), item["scenario_id"])

    def test_audit_rejects_wolf_death_followups_and_answer_reveals(self) -> None:
        """The two defects found in v1 remain fail-closed regression cases."""
        inputs, labels = build_set("FB", analysis=True)
        established_index = next(
            i
            for i, label in enumerate(labels)
            if label["scenario_class"] == "established_pp" and label["perspective_role"] == "POSSESSED"
        )
        item = deepcopy(inputs[established_index])
        label = labels[established_index]
        snapshot = item["input"]["snapshot"]
        wolf = next(name for name, role in label["hidden_roles"].items() if role == "WEREWOLF")
        execution = next(event for event in snapshot["public_history"] if event["type"] == "EXECUTE")
        originally_executed = execution["target"]
        execution["target"] = wolf
        snapshot["status_map"][originally_executed] = "ALIVE"
        snapshot["status_map"][wolf] = "DEAD"
        errors = audit_pair(item, label, set())
        self.assertTrue(any("event occurs after werewolf death" in error for error in errors))

        near_index = next(
            i
            for i, candidate in enumerate(labels)
            if candidate["scenario_class"] == "near_control"
        )
        item = deepcopy(inputs[near_index])
        label = labels[near_index]
        talk = next(event for event in item["input"]["snapshot"]["public_history"] if event["type"] == "TALK")
        talk["text"] = "This is not PP; nobody should reveal."
        errors = audit_pair(item, label, set())
        self.assertTrue(any("reveals the negative label" in error for error in errors))

    def test_confirmation_set_is_disjoint_and_excluded(self) -> None:
        """The 12 route checks can never enter the confirmatory denominator."""
        analysis, _ = build_set("FB", analysis=True)
        confirmation, labels = build_set("QC", analysis=False)
        self.assertEqual(len(confirmation), 12)
        self.assertTrue(all(label["exclude_from_confirmatory_analysis"] for label in labels))
        self.assertFalse(
            {item["input_sha256"] for item in analysis} & {item["input_sha256"] for item in confirmation},
        )

    def test_deterministic_scorer_extracts_co_target_and_vote_match(self) -> None:
        """A compliant fixture traverses the deterministic component scorer."""
        inputs, labels = build_set("QC", analysis=False)
        item = next(item for item in inputs if item["scenario_id"].startswith("QC-E-POS"))
        label = next(label for label in labels if label["scenario_id"] == item["scenario_id"])
        target = label["valid_village_targets"][0]
        ally = label["correct_ally"]
        record = {
            "scenario_id": item["scenario_id"],
            "condition": "P+",
            "input_sha256": item["input_sha256"],
            "success": True,
            "talk": f"I'm the possessed. {ally}, match my vote on {target}.",
            "vote": target,
        }
        score = score_record(record, item, label)["deterministic"]
        self.assertEqual(score["role_co"], "POSSESSED")
        self.assertEqual(score["declared_vote_target"], target)
        self.assertTrue(score["talk_vote_match"])
        self.assertTrue(score["ally_preserved"])
        self.assertTrue(score["valid_village_target"])

    def test_v2_corpus_is_deterministic_and_disjoint_from_v1(self) -> None:
        """The rerun uses new scenario identities and no legacy input hash."""
        spec = yaml.safe_load(V2_SPEC_PATH.read_text(encoding="utf-8"))
        v2_inputs, v2_labels = build_set_v2(spec, analysis=True)
        v2_confirmation, v2_confirmation_labels = build_set_v2(spec, analysis=False)
        legacy_inputs, _ = build_set("FB", analysis=True)
        legacy_confirmation, _ = build_set("QC", analysis=False)
        self.assertEqual(len(v2_inputs), 120)
        self.assertEqual(len(v2_confirmation), 12)
        self.assertTrue(all(label["exclude_from_confirmatory_analysis"] for label in v2_confirmation_labels))
        v2_hashes = {row["input_sha256"] for row in [*v2_inputs, *v2_confirmation]}
        legacy_hashes = {row["input_sha256"] for row in [*legacy_inputs, *legacy_confirmation]}
        self.assertEqual(len(v2_hashes), 132)
        self.assertFalse(v2_hashes & legacy_hashes)
        for item, label in zip(v2_inputs, v2_labels, strict=True):
            if label["scenario_class"] != "established_pp" or label["perspective_role"] != "WEREWOLF":
                continue
            ally = label["correct_ally"]
            self.assertTrue(any(
                event.get("type") == "TALK"
                and event.get("actor") == ally
                and "possessed" in event.get("text", "").casefold()
                for event in item["input"]["snapshot"]["public_history"]
            ))
        forbidden = set(spec["future_information_forbidden_keys"])
        self.assertEqual(
            [
                error
                for item, label in zip(v2_inputs, v2_labels, strict=True)
                for error in audit_pair(item, label, forbidden)
            ],
            [],
        )
