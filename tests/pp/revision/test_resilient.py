"""No-network regressions for bounded retries, admission audit and continuation."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from experiments.pp.revision import normal
from experiments.pp.revision.common import HERE, ROOT, read, write, verify_imported
from experiments.pp.revision.prepare import allocation

sys.path.insert(0, str(ROOT / "src"))
from utils import request_audit


class ResilientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.cfg = yaml.safe_load((HERE / "spec-resume8.yml").read_text())
        self.cfg["normal"].update(
            blocks=8, start_deadline="2099-01-01T00:00:00+00:00", data_deadline="2099-01-02T00:00:00+00:00"
        )
        (self.run / "spec.yml").write_text(yaml.safe_dump(self.cfg))
        self.schedule = allocation(8, 42)
        write(self.run / "schedule.json", self.schedule)
        write(self.run / "state.json", {"started": [], "schedule_finished": False})
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("process forbidden")))
        self.enterContext(patch.object(normal, "verify", return_value={"kind": "B"}))
        self.enterContext(patch.object(normal, "load_credentials"))
        self.enterContext(patch.object(normal, "available_memory_gib", return_value=100))
        self.enterContext(patch.object(normal.time, "sleep"))

    def test_unfinished_request_and_action_timeout_are_never_accepted(self):
        config = {"experiment": {"request_audit": True}, "log": {"output_dir": str(self.run / "logs/revisionSeer")}}
        admission = request_audit.begin(config, "TALK")
        self.assertEqual(normal.operational_failures(self.run / "logs", live=True), [])
        self.assertIn("revisionSeer:unfinished_request", normal.operational_failures(self.run / "logs"))
        request_audit.finish(admission)
        self.assertEqual(normal.operational_failures(self.run / "logs"), [])
        request_audit.timed_out(config, "TALK")
        self.assertIn("revisionSeer:action_timeout", normal.operational_failures(self.run / "logs", live=True))

    def test_legacy_timeout_detected_without_call_failure_record(self):
        (self.run / "revisionPossessed.txt").write_text("WARNING - Action timed out: VOTE\n")
        self.assertIn("revisionPossessed:action_timeout", normal.operational_failures(self.run / "logs"))

    def test_transient_failure_retries_only_its_row_and_resume_is_free(self):
        def child(command, output, timeout):
            block, cell, attempt = command[-4:-1]
            first = self.schedule[0]
            fail = block == first["block_id"] and cell == first["conditions"][0] and attempt == "1"
            write(output.parent / "result.json", {"status": "retryable" if fail else "complete"})
            return 0

        with patch.object(normal, "child", side_effect=child) as calls:
            normal.run(self.run, execute=True)
            self.assertEqual(calls.call_count, 33)
            self.assertTrue(read(self.run / "state.json")["schedule_finished"])
            normal.run(self.run, execute=True)
            self.assertEqual(calls.call_count, 33, "completed games were charged again")

    def test_memory_guard_pauses_without_launch_or_block_registration(self):
        with patch.object(normal, "available_memory_gib", return_value=7), patch.object(normal, "child") as calls:
            with self.assertRaisesRegex(RuntimeError, "low available memory"):
                normal.run(self.run, execute=True)
            calls.assert_not_called()
        self.assertEqual(read(self.run / "state.json")["started"], [])
        self.assertTrue(read(self.run / "state.json")["memory_paused"])

    def test_repair_only_never_launches_other_rows(self):
        row = self.schedule[0]["block_id"] + "-" + self.schedule[0]["conditions"][0]
        write(self.run / "lineage/continuation.json", {"repair_rows": [row]})

        def child(command, output, timeout):
            self.assertEqual("-".join(command[-4:-2]), row)
            write(output.parent / "result.json", {"status": "complete"})
            return 0

        with patch.object(normal, "child", side_effect=child) as calls:
            normal.run(self.run, execute=True, repair_only=True)
            self.assertEqual(calls.call_count, 1)
            self.assertFalse(read(self.run / "state.json")["schedule_finished"])

    def test_retries_remain_bounded_across_restarts(self):
        row = self.schedule[0]["block_id"] + "-" + self.schedule[0]["conditions"][0]
        write(self.run / "lineage/continuation.json", {"repair_rows": [row]})

        def child(command, output, timeout):
            write(output.parent / "result.json", {"status": "retryable"})
            return 0

        with patch.object(normal, "child", side_effect=child) as calls:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "Retry budget exhausted"):
                    normal.run(self.run, execute=True, repair_only=True)
            self.assertEqual(calls.call_count, 3)

    def test_imported_evidence_tampering_rejected(self):
        write(self.run / "lineage/imported-evidence.json", {"retained.json": "wrong"})
        write(self.run / "retained.json", {"status": "complete"})
        with self.assertRaisesRegex(ValueError, "Imported successful evidence changed"):
            verify_imported(self.run)

    def test_authentication_and_unknown_errors_remain_fatal(self):
        for error in ("AuthenticationError", "RateLimitError", "BadRequestError", "invalid_call_log"):
            self.assertFalse(normal.transient_failures([f"team:generation_failed:{error}"]))
        self.assertTrue(normal.transient_failures(["team:generation_failed:APIConnectionError"]))


if __name__ == "__main__":
    unittest.main()
