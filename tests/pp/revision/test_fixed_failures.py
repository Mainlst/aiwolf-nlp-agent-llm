"""No-network regressions for A request checkpoints and permanent failure stops."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import yaml
from openai import APIConnectionError

from experiments.pp.revision import fixed
from experiments.pp.revision.common import HERE, ROOT, jsonl, read, write
from experiments.pp.revision.prepare import policies

sys.path.insert(0, str(ROOT / "src"))
from agent.agent import Agent
from langchain_core.messages import AIMessage


class FixedFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.item = jsonl(ROOT / "experiments/pp/fixed_boards_v2/generated/inputs.jsonl")[0]
        self.label = jsonl(ROOT / "experiments/pp/fixed_boards_v2/generated/labels.jsonl")[0]
        self.target = next(
            name
            for name, status in self.item["input"]["snapshot"]["status_map"].items()
            if status == "ALIVE" and name != self.item["input"]["perspective"]["agent"]
        )
        self.settings = yaml.safe_load((HERE / "spec.yml").read_text())
        self.settings["fixed"].update(data_deadline="2099-01-01T00:00:00+00:00", max_parallel=1)
        (self.run / "spec.yml").write_text(yaml.safe_dump(self.settings))
        for name, item in (("inputs", self.item), ("labels", self.label)):
            (self.run / f"{name}.jsonl").write_text(json.dumps(item) + "\n")
        (self.run / "policies").mkdir()
        for name, config in policies().items():
            (self.run / "policies" / f"{name}.yml").write_text(yaml.safe_dump(config))
        (self.run / "judge-spec").mkdir()
        for name in ("config.yml", "rubric.md"):
            (self.run / "judge-spec" / name).write_text((ROOT / "experiments/pp/fixed_boards/judge" / name).read_text())
        self.groups = [
            {
                "group_id": f"G{r}",
                "scenario_id": self.item["scenario_id"],
                "talk_bit": "1",
                "repetition": r,
                "vote_bits": ["1", "0"],
            }
            for r in (1, 2)
        ]
        write(self.run / "schedule.json", self.groups)
        write(self.run / "state.json", {"started": ["G1", "G2"], "schedule_finished": False})
        self.calls = 0
        self.failures = set()
        case = self

        class FakeModel:
            temperature = 1.0

            def invoke(self, messages):
                case.calls += 1
                if case.calls in case.failures:
                    raise APIConnectionError(request=httpx.Request("POST", "https://offline.invalid"))
                return AIMessage(
                    content=case.target,
                    usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    response_metadata={"model_name": "gpt-5.6-luna"},
                )

        def initialize(agent):
            agent.llm_model = FakeModel()

        for mock in (
            patch.object(Agent, "initialize", initialize),
            patch.object(fixed, "verify", return_value={"kind": "A", "purpose": "smoke"}),
            patch.object(fixed, "load_credentials"),
            patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
            patch("openai.OpenAI", return_value=object()),
            patch.dict("os.environ", {"OPENAI_API_KEY": "offline-test"}),
            patch("builtins.print"),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def interrupt_summary(self, summary):
        def interrupted(path, data):
            if path == summary:
                raise KeyboardInterrupt("interrupted after attempt result was saved")
            return write(path, data)

        return patch.object(fixed, "write", side_effect=interrupted)

    def fake_child(self, command, *_):
        fixed.group_worker(Path(command[3]), command[-2])
        return 0

    def generate_all(self):
        for group in self.groups:
            fixed.group_worker(self.run, group["group_id"])

    def judge_result(self, _client, blind, cfg, _rubric):
        return {
            "success": True,
            "response_model_id": cfg["model"],
            "error": None,
            "usage": {},
            "result": {
                field: {
                    "label": False,
                    "evidence": [{"event_id": "target-talk", "quote": blind["target_talk"]["text"]}],
                    "reason": "Offline fixture.",
                }
                for field in ("pp_recognized", "ally_recognized", "vote_coordination")
            },
        }

    def judge_budget(self):
        return fixed.JudgeBudget(self.run, yaml.safe_load((self.run / "judge-spec/config.yml").read_text()))

    def test_saved_talk_and_vote_recover_without_new_calls(self):
        for group, stage in zip(self.groups, ("talk", "vote")):
            with self.subTest(stage=stage):
                summary = self.run / "groups" / group["group_id"] / ("talk.json" if stage == "talk" else "vote1.json")
                initial = None
                if stage == "vote":
                    initial = fixed.generate_stage(self.run, group, "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")[
                        "state"
                    ]
                with self.interrupt_summary(summary), self.assertRaises(KeyboardInterrupt):
                    fixed.generate_stage(self.run, group, stage, "1", initial, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
                calls = self.calls
                result = fixed.generate_stage(self.run, group, stage, "1", initial, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
                self.assertEqual(result["status"], "complete")
                self.assertEqual(self.calls, calls)
                self.assertEqual(read(summary), result)

    def test_last_generation_attempt_recovers_after_deadline(self):
        group = self.groups[0]
        self.failures = {1}
        summary = self.run / "groups/G1/talk.json"
        with self.interrupt_summary(summary), self.assertRaises(KeyboardInterrupt):
            fixed.generate_stage(self.run, group, "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        self.assertEqual(self.calls, 2)
        with (
            patch("experiments.pp.revision.common.now", return_value=datetime(2100, 1, 1, tzinfo=timezone.utc)),
            patch.object(fixed, "now", return_value=datetime(2100, 1, 1, tzinfo=timezone.utc)),
        ):
            result = fixed.generate_stage(self.run, group, "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(self.calls, 2)
        self.assertFalse(fixed.stop_records(self.run))

    def test_corrupt_saved_generation_is_never_reused_or_retried(self):
        group = self.groups[0]
        with self.interrupt_summary(self.run / "groups/G1/talk.json"), self.assertRaises(KeyboardInterrupt):
            fixed.generate_stage(self.run, group, "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        result_path = self.run / "groups/G1/talk/1/result.json"
        result = read(result_path)
        result["game_id"] = "wrong-game"
        write(result_path, result)
        with self.assertRaisesRegex(ValueError, "provenance"):
            fixed.generate_stage(self.run, group, "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        self.assertEqual(self.calls, 1)
        self.assertEqual(fixed.stop_records(self.run)[0]["reason"], "generation_provenance_mismatch")

    def test_generation_exhaustion_stops_later_groups_and_refuses_resume(self):
        self.failures = {1, 2}
        with patch.object(fixed, "child", side_effect=self.fake_child):
            with self.assertRaisesRegex(ValueError, "permanent stop"):
                fixed.run(self.run, execute=True)
            self.assertEqual(self.calls, 2)
            state = read(self.run / "state.json")
            self.assertTrue(state["permanent_stop"])
            self.assertEqual(state["stop_reasons"][0]["reason"], "attempts_exhausted")
            with self.assertRaisesRegex(ValueError, "permanent stop"):
                fixed.run(self.run, execute=True)
            self.assertEqual(self.calls, 2)
        self.assertFalse((self.run / "groups/G2/identity.json").exists())

    def test_parent_recovers_successful_checkpoint_after_nonzero_worker_exit(self):
        interrupted = False

        def child(command, *_):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                try:
                    with self.interrupt_summary(self.run / "groups/G1/talk.json"):
                        return self.fake_child(command)
                except KeyboardInterrupt:
                    return 130
            return self.fake_child(command)

        with patch.object(fixed, "child", side_effect=child):
            fixed.run(self.run, execute=True)
            self.assertEqual(read(self.run / "groups/G1/talk.json")["attempt"], 1)
            self.assertFalse(read(self.run / "state.json").get("permanent_stop"))
            self.assertEqual(self.calls, 4)
            fixed.run(self.run, execute=True)
        self.assertEqual(self.calls, 6)
        self.assertEqual(len(fixed.all_units(self.run)), 4)

    def test_initialization_error_stops_later_groups_with_existing_start(self):
        def child(command, *_):
            try:
                return self.fake_child(command)
            except RuntimeError:
                return 1

        with (
            patch.object(Agent, "initialize", side_effect=RuntimeError("offline initialization failure")),
            patch.object(
                fixed,
                "child",
                side_effect=child,
            ),
            self.assertRaisesRegex(ValueError, "permanent stop"),
        ):
            fixed.run(self.run, execute=True)
        self.assertTrue((self.run / "groups/G1/talk/1/start.json").exists())
        self.assertFalse((self.run / "groups/G2/identity.json").exists())
        self.assertEqual(self.calls, 0)
        self.assertTrue(read(self.run / "state.json")["critical_stop"])

    def test_vote_exhaustion_stops_other_branch(self):
        self.failures = {2, 3}
        fixed.group_worker(self.run, "G1")
        self.assertEqual(self.calls, 3)
        self.assertEqual(fixed.stop_records(self.run)[0]["stage"], "vote1")
        self.assertFalse((self.run / "groups/G1/vote0").exists())

    def test_recovery_scans_later_exhaustion_before_new_generation(self):
        self.failures = {1, 2}
        # Simulate a crash between the last missing result and its stop marker.
        with patch.object(fixed, "record_stop"):
            fixed.group_worker(self.run, "G2")
        self.assertFalse(fixed.stop_records(self.run))
        with (
            patch.object(fixed, "child", side_effect=self.fake_child),
            self.assertRaisesRegex(ValueError, "permanent stop"),
        ):
            fixed.run(self.run, execute=True)
        self.assertEqual(self.calls, 2)
        self.assertTrue(read(self.run / "state.json")["permanent_stop"])

    def test_saved_judge_last_attempt_recovers_after_deadline(self):
        self.generate_all()
        group = self.groups[0]
        calls = 0

        def judge(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"success": False, "error": {"type": "APIConnectionError"}, "usage": {}}
            return self.judge_result(*args)

        with patch("experiments.pp.fixed_boards.pp_judge.call_judge", side_effect=judge):
            with self.interrupt_summary(self.run / "judge/G1/R1.json"), self.assertRaises(KeyboardInterrupt):
                fixed.judge_group(self.run, group, object(), self.judge_budget())
            self.assertEqual(calls, 2)
            with (
                patch("experiments.pp.revision.common.now", return_value=datetime(2100, 1, 1, tzinfo=timezone.utc)),
                patch.object(fixed, "now", return_value=datetime(2100, 1, 1, tzinfo=timezone.utc)),
            ):
                fixed.judge_group(self.run, group, object(), self.judge_budget())
        self.assertEqual(calls, 2)
        self.assertEqual(read(self.run / "judge/G1/R1.json")["attempt"], 2)
        self.assertFalse(fixed.stop_records(self.run))

    def test_judge_exhaustion_stops_later_repetitions_and_groups(self):
        self.generate_all()
        failure = {"success": False, "error": {"type": "APIConnectionError"}, "usage": {}}
        with patch("experiments.pp.fixed_boards.pp_judge.call_judge", return_value=failure) as call:
            with self.assertRaisesRegex(ValueError, "permanent stop"):
                fixed.run(self.run, execute=True, judge=True)
            self.assertEqual(call.call_count, 2)
            state = read(self.run / "state.json")
            self.assertTrue(state["permanent_stop"])
            self.assertEqual(state["stop_reasons"][0]["stage"], "judge-R1")
            with self.assertRaisesRegex(ValueError, "permanent stop"):
                fixed.run(self.run, execute=True, judge=True)
            self.assertEqual(call.call_count, 2)
        self.assertFalse((self.run / "judge/G1/R2-attempt1").exists())
        self.assertFalse((self.run / "judge/G2/R1-attempt1").exists())

    def test_parallel_judge_preserves_inflight_result_but_admits_no_followup(self):
        self.generate_all()
        self.settings["fixed"]["max_parallel"] = 2
        (self.run / "spec.yml").write_text(yaml.safe_dump(self.settings))
        second_started, stopped = threading.Event(), threading.Event()
        record_stop = fixed.record_stop

        def stop(*args):
            record_stop(*args)
            stopped.set()

        def judge(client, blind, cfg, rubric):
            first_id = read(self.run / "judge/G1/input.json")["blind_id"]
            if blind["blind_id"] == first_id:
                self.assertTrue(second_started.wait(5))
                return {"success": False, "error": {"type": "APIConnectionError"}, "usage": {}}
            second_started.set()
            self.assertTrue(stopped.wait(5))
            return self.judge_result(client, blind, cfg, rubric)

        with (
            patch.object(fixed, "record_stop", side_effect=stop),
            patch(
                "experiments.pp.fixed_boards.pp_judge.call_judge",
                side_effect=judge,
            ) as call,
        ):
            with self.assertRaisesRegex(ValueError, "permanent stop"):
                fixed.run(self.run, execute=True, judge=True)
        self.assertEqual(call.call_count, 3)
        self.assertTrue(read(self.run / "judge/G2/R1.json")["success"])
        self.assertFalse((self.run / "judge/G2/R2-attempt1").exists())

    def test_saved_generation_critical_stops_before_retry(self):
        group = self.groups[0]
        with (
            patch.object(fixed, "generation_call_critical", return_value=True),
            patch.object(
                fixed,
                "record_stop",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            fixed.generate_stage(self.run, group, "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        result = fixed.generate_stage(self.run, group, "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        self.assertEqual(result["status"], "critical")
        self.assertEqual(self.calls, 1)
        self.assertEqual(fixed.stop_records(self.run)[0]["reason"], "critical_generation_result")

    def test_saved_judge_critical_is_not_ignored(self):
        self.generate_all()
        fatal = {"success": False, "error": {"type": "AuthenticationError"}, "usage": {}}
        with patch("experiments.pp.fixed_boards.pp_judge.call_judge", return_value=fatal) as call:
            with (
                patch.object(fixed, "record_stop", side_effect=KeyboardInterrupt),
                self.assertRaises(KeyboardInterrupt),
            ):
                fixed.judge_group(self.run, self.groups[0], object(), self.judge_budget())
            with self.assertRaisesRegex(ValueError, "permanent stop"):
                fixed.run(self.run, execute=True, judge=True)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(read(self.run / "state.json")["critical_stop"])

    def test_saved_judge_input_mismatch_stops_before_retry(self):
        self.generate_all()
        with patch("experiments.pp.fixed_boards.pp_judge.call_judge", side_effect=self.judge_result) as call:
            with self.interrupt_summary(self.run / "judge/G1/R1.json"), self.assertRaises(KeyboardInterrupt):
                fixed.judge_group(self.run, self.groups[0], object(), self.judge_budget())
            saved = read(self.run / "judge/G1/input.json")
            saved["target_talk"]["text"] = "Changed TALK"
            write(self.run / "judge/G1/input.json", saved)
            with self.assertRaisesRegex(ValueError, "judge input"):
                fixed.judge_group(self.run, self.groups[0], object(), self.judge_budget())
        self.assertEqual(call.call_count, 1)

    def test_deadline_and_budget_stop_without_consuming_attempts(self):
        with (
            patch("experiments.pp.revision.common.now", return_value=datetime(2100, 1, 1, tzinfo=timezone.utc)),
            patch.object(fixed, "now", return_value=datetime(2100, 1, 1, tzinfo=timezone.utc)),
        ):
            self.assertIsNone(
                fixed.generate_stage(self.run, self.groups[0], "talk", "1", None, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
            )
        self.assertEqual(self.calls, 0)
        self.assertFalse(fixed.stop_records(self.run))
        self.generate_all()
        budget = self.judge_budget()
        budget.config = deepcopy(budget.config)
        budget.config["batch_cost_limit_usd"] = 0
        with patch("experiments.pp.fixed_boards.pp_judge.call_judge") as call:
            fixed.judge_group(self.run, self.groups[0], object(), budget)
        call.assert_not_called()
        self.assertFalse(fixed.stop_records(self.run))
        self.assertFalse((self.run / "judge/G1/R1-attempt1").exists())


if __name__ == "__main__":
    unittest.main()
