from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiments.pp.fixed_boards.run_analysis import (
    EXPECTED_MODEL,
    live_identity_errors,
    make_schedule,
    unit_id,
    validate_attempt,
)


class PPFixedAnalysisRunnerTest(unittest.TestCase):
    def test_schedule_is_deterministic_balanced_and_unique(self) -> None:
        inputs = [{"scenario_id": f"S{i}"} for i in range(3)]
        first = make_schedule(inputs)
        second = make_schedule(inputs)
        first_keys = [(item["scenario_id"], condition, repetition) for item, condition, repetition in first]
        second_keys = [(item["scenario_id"], condition, repetition) for item, condition, repetition in second]
        self.assertEqual(first_keys, second_keys)
        self.assertEqual(len(first_keys), 12)
        self.assertEqual(len(set(first_keys)), 12)

    def test_order_seed_changes_order_but_not_units(self) -> None:
        inputs = [{"scenario_id": f"S{i}"} for i in range(20)]
        first = make_schedule(inputs, order_seed=20260815)
        second = make_schedule(inputs, order_seed=20260817)
        first_keys = [(item["scenario_id"], condition, repetition) for item, condition, repetition in first]
        second_keys = [(item["scenario_id"], condition, repetition) for item, condition, repetition in second]
        self.assertNotEqual(first_keys, second_keys)
        self.assertEqual(set(first_keys), set(second_keys))

    def test_unit_id_is_stable_and_safe(self) -> None:
        self.assertEqual(unit_id("A-001", "P+", 2), "A-001__PPLUS__R2")
        self.assertEqual(unit_id("A-001", "P-PP", 1), "A-001__PMINUSPP__R1")

    def test_validation_rejects_wrong_provider_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)
            (log_dir / "llm_calls.jsonl").write_text(
                '{"request":"TALK","game_id":"G","condition":"P+","response_model_id":"wrong","success":true}\n',
                encoding="utf-8",
            )
            record = {
                "game_id": "G",
                "condition": "P+",
                "input_sha256": "H",
                "configured_model": EXPECTED_MODEL,
                "generation_success": False,
            }
            result = validate_attempt(record, log_dir, "H")
            self.assertTrue(result["critical_errors"])

    def test_validation_accepts_complete_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)
            calls = "".join(
                f'{{"request":"{request}","game_id":"G","condition":"P+",'
                f'"response_model_id":"{EXPECTED_MODEL}","success":true}}\n'
                for request in ("TALK", "VOTE")
            )
            usage = "".join(
                f'{{"request":"{request}","game_id":"G","response_model_id":"{EXPECTED_MODEL}"}}\n'
                for request in ("TALK", "VOTE")
            )
            (log_dir / "llm_calls.jsonl").write_text(calls, encoding="utf-8")
            (log_dir / "llm_usage.jsonl").write_text(usage, encoding="utf-8")
            record = {
                "game_id": "G",
                "condition": "P+",
                "input_sha256": "H",
                "configured_model": EXPECTED_MODEL,
                "generation_success": True,
            }
            result = validate_attempt(record, log_dir, "H")
            self.assertTrue(result["passed"])

    def test_live_identity_check_stops_wrong_talk_model_before_vote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)
            (log_dir / "llm_calls.jsonl").write_text(
                '{"request":"TALK","game_id":"G","condition":"P+","response_model_id":"wrong","success":true}\n',
                encoding="utf-8",
            )
            self.assertTrue(live_identity_errors(log_dir, "G", "P+"))

    def test_live_identity_check_stops_connection_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)
            (log_dir / "llm_calls.jsonl").write_text(
                '{"request":"TALK","game_id":"G","condition":"P+",'
                '"response_model_id":null,"success":false,"error_type":"AuthenticationError"}\n',
                encoding="utf-8",
            )
            self.assertTrue(live_identity_errors(log_dir, "G", "P+"))


if __name__ == "__main__":
    unittest.main()
