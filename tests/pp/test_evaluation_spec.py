"""Tests for the preregistered PP inferential design."""

from __future__ import annotations

from unittest import TestCase

import yaml


from tests import ROOT

SPEC_PATH = ROOT / "experiments" / "pp" / "evaluation-spec.yml"
V2_SPEC_PATH = ROOT / "experiments" / "pp" / "v2" / "evaluation-spec.yml"


class PPEvaluationSpecTest(TestCase):
    """Keep sample counts, pairing, and multiplicity rules unambiguous."""

    def setUp(self) -> None:
        self.spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))

    def test_normal_game_count_and_pairing_are_consistent(self) -> None:
        """The target is 400 games arranged as 200 balanced condition pairs."""
        normal = self.spec["stopping_rules"]["normal_games"]
        self.assertEqual(normal["total_planned_games"], 400)
        self.assertEqual(normal["planned_games_per_condition"], 200)
        self.assertEqual(normal["matched_pairs"], 200)
        self.assertEqual(normal["total_planned_games"], 2 * normal["matched_pairs"])
        self.assertEqual(normal["games_per_block"], 2 * normal["matched_pairs_per_block"])

    def test_primary_endpoints_have_preregistered_familywise_control(self) -> None:
        """Both confirmatory p-values belong to one Holm-adjusted family."""
        statistics = self.spec["statistics"]
        familywise = statistics["familywise_error"]
        self.assertEqual(familywise["method"], "Holm")
        self.assertEqual(familywise["alpha"], 0.05)
        self.assertEqual(familywise["family"], statistics["primary_confirmatory_endpoints"])

    def test_fixed_boards_use_content_clusters_and_condition_pairs(self) -> None:
        """Role variants and repetitions cannot inflate the inferential sample size."""
        fixed = self.spec["statistics"]["fixed_board"]
        self.assertEqual(fixed["condition_pair_key"], "scenario_id")
        self.assertEqual(fixed["analysis_clusters"], 60)
        self.assertEqual(fixed["analysis_clusters"], fixed["established_pp_clusters"] + fixed["near_control_clusters"])
        self.assertIn("never counted as independent", fixed["repetitions"])


class PPV2EvaluationSpecTest(TestCase):
    """Freeze the corrected endpoint and legacy separation before v2 calls."""

    def setUp(self) -> None:
        self.spec = yaml.safe_load(V2_SPEC_PATH.read_text(encoding="utf-8"))

    def test_composite_has_exactly_six_registered_components(self) -> None:
        expected = {
            "pp_recognized", "correct_co_or_response", "ally_preserved",
            "valid_target_selected", "vote_coordination", "talk_vote_consistent",
        }
        self.assertEqual(set(self.spec["composite_pp_success"]["required_components"]), expected)
        self.assertEqual(self.spec["composite_pp_success"]["explicitly_not_a_required_component"], "ally_recognized")

    def test_repeat_rules_and_legacy_exclusion_are_explicit(self) -> None:
        self.assertIn("both repetitions", self.spec["composite_pp_success"]["repeat_rule"])
        self.assertIn("either repetition", self.spec["false_pp"]["repeat_rule"])
        self.assertTrue(self.spec["exclusions"]["legacy_v1_fixed_board_p_value_from_holm_family"])
        self.assertEqual(
            self.spec["analysis"]["primary_holm_family"],
            ["v2_composite_pp_success", "v2_normal_game_werewolf_team_win_rate"],
        )
