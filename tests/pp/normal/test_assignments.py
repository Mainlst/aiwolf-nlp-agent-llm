"""Tests for the PP v2 matched normal-game allocation."""

from __future__ import annotations

from collections import Counter, defaultdict
from unittest import TestCase

from experiments.pp.v2.normal.generate_assignments import build


class PPV2NormalAllocationTest(TestCase):
    def test_allocation_is_deterministic_balanced_and_paired(self) -> None:
        rows = build()
        self.assertEqual(rows, build())
        self.assertEqual(len(rows), 400)
        self.assertEqual(Counter(row["condition"] for row in rows), Counter({"P+": 200, "P-PP": 200}))
        pairs = defaultdict(list)
        for row in rows:
            pairs[row["match_pair_id"]].append(row)
        self.assertEqual(len(pairs), 200)
        for pair in pairs.values():
            self.assertEqual({row["condition"] for row in pair}, {"P+", "P-PP"})
            for field in ("planned_role", "target_seat", "opponent_pool", "analysis_block", "optimizer_seed"):
                self.assertEqual(len({row[field] for row in pair}), 1)

    def test_each_condition_balances_role_seat_and_block(self) -> None:
        rows = build()
        for condition in ("P+", "P-PP"):
            subset = [row for row in rows if row["condition"] == condition]
            self.assertEqual(
                Counter(row["planned_role"] for row in subset),
                Counter({"POSSESSED": 100, "WEREWOLF": 100}),
            )
            self.assertEqual(Counter(row["target_seat"] for row in subset), Counter({str(i): 40 for i in range(1, 6)}))
            self.assertEqual(
                Counter(row["analysis_block"] for row in subset),
                Counter({f"{i:02d}": 10 for i in range(1, 21)}),
            )
