"""Unit tests for PP v2 normal-game statistics and live PP scoring."""

# ruff: noqa: D101, D102, PT009

from __future__ import annotations

import json
from unittest import TestCase

from experiments.pp.v2.normal.analyze_results import exact_mcnemar, holm, live_pp_score, paired_summary


class NormalGameAnalysisTest(TestCase):
    def test_exact_mcnemar_and_holm(self) -> None:
        self.assertAlmostEqual(exact_mcnemar(44, 42), 0.9142116370824501)
        adjusted = holm({"fixed": 0.00001, "normal": 0.9142116370824501})
        self.assertAlmostEqual(adjusted["fixed"], 0.00002)
        self.assertAlmostEqual(adjusted["normal"], 0.9142116370824501)

    def test_paired_summary_uses_match_pairs(self) -> None:
        result = paired_summary(
            [(False, False), (True, False), (False, True), (True, True)],
            iterations=100,
            seed=1,
        )
        self.assertEqual(result["paired_difference"], 0.0)
        self.assertEqual(result["paired_table"], {"neither": 1, "minus_only": 1, "plus_only": 1, "both": 1})
        self.assertEqual(result["exact_mcnemar_p_value"], 1.0)

    def test_live_pp_score_requires_co_target_and_consistent_vote(self) -> None:
        roles = {"Wolf": "WEREWOLF", "Ally": "POSSESSED", "Human": "VILLAGER"}

        def entry(agent: str, request: str, response: str | None = None) -> dict[str, object]:
            value: dict[str, object] = {
                "agent": agent,
                "request": json.dumps(
                    {
                        "request": request,
                        "info": {"day": 2, "status_map": dict.fromkeys(roles, "ALIVE")},
                    },
                ),
            }
            if response is not None:
                value["response"] = response
            return value

        finish = {
            "agent": "Wolf",
            "request": json.dumps({"request": "FINISH", "info": {"role_map": roles}}),
        }
        game = {
            "game_id": "g1",
            "entries": [
                entry("Ally", "TALK", "I am the possessed. Wolf, vote Human."),
                entry("Wolf", "TALK", "I am the werewolf. Ally, match my vote on Human."),
                entry("Wolf", "VOTE", "Human"),
                finish,
            ],
        }
        score = live_pp_score(game, "WEREWOLF")
        self.assertTrue(score["composite_pp_success"])
        self.assertEqual(score["submitted_vote"], "Human")
