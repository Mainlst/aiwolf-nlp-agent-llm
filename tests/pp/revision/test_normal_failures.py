"""Offline regressions for B provider failures and final server-log races."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from experiments.pp.revision import normal
from experiments.pp.revision.common import HERE, POLICIES, TEAMS, read, write
from experiments.pp.revision.prepare import allocation, dump


class NormalFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.block = allocation(1, 1)[0]
        self.cell = "G11"
        self.folder = self.run / "attempts" / f"{self.block['block_id']}-{self.cell}" / "1"
        self.started = datetime.now(timezone.utc)
        self.elapsed = 0.0
        write(self.run / "schedule.json", [self.block])
        write(self.run / "state.json", {"started": [self.block["block_id"]], "schedule_finished": False})
        write(
            self.folder / "start.json",
            {"port": 19080, "timeout": 900, "started_at": self.started.isoformat()},
        )
        dump(
            self.run / "servers" / f"{self.block['block_id']}.yml",
            {"server": {"web_socket": {}}, "matching": {}, "json_logger": {}},
        )
        write(self.run / "optimizers" / f"{self.block['block_id']}.json", {})
        for cell in ("G00", "G10", "G01", "G11"):
            for team in TEAMS:
                dump(self.run / "cells" / cell / f"{team}.yml", {"web_socket": {}, "log": {}})
        self.game = {
            "game_id": "g",
            "win_side": "WEREWOLF",
            "agents": [{"idx": row["seat"], "team": row["team"], "role": row["role"]} for row in self.block["roster"]],
            "entries": [{"agent": team, "request": json.dumps({"request": "FINISH"})} for team in TEAMS],
        }

    def logs(self, error=None, folder=None, cell=None):
        for index, team in enumerate(TEAMS):
            call = {
                "game_id": "g",
                "condition": POLICIES[(cell or self.cell)[index + 1]] if index < 2 else "",
                "model": {"model": "gpt-5.6-luna", "temperature": "1.0", "effective_temperature": "1.0"},
                "response_model_id": None if error else "gpt-5.6-luna",
                "success": not bool(error),
                "error_type": error,
                # A successful but unsuitable model answer remains an outcome.
                "response": "I cannot choose a valid player.",
            }
            path = (folder or self.folder) / "logs" / team / "llm_calls.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(call) + "\n")

    def publish_game(self):
        write(self.folder / "json/g.json", self.game)

    def worker_mocks(self, on_client_poll=None, on_stop=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("network forbidden")))
        stack.enter_context(
            patch.object(
                normal,
                "verify",
                return_value={"kind": "B", "server_binary": "unused", "server_cwd": str(self.run)},
            )
        )
        stack.enter_context(patch.object(normal, "load_credentials"))
        stack.enter_context(patch.object(normal, "port_is_open", return_value=False))
        stack.enter_context(patch.object(normal, "wait_for_server"))
        stack.enter_context(patch.object(normal, "now", return_value=self.started))
        stack.enter_context(patch.object(normal.time, "monotonic", side_effect=lambda: self.elapsed))

        def sleep(seconds):
            self.elapsed += seconds

        stack.enter_context(patch.object(normal.time, "sleep", side_effect=sleep))

        class Process:
            def __init__(self, is_server):
                self.is_server = is_server

            def poll(self):
                if not self.is_server and on_client_poll:
                    on_client_poll()
                return None if self.is_server else 0

        processes = []

        def popen(command, **kwargs):
            process = Process(command[0] == "unused")
            processes.append(process)
            return process

        spawned = stack.enter_context(patch.object(normal.subprocess, "Popen", side_effect=popen))
        stopped = stack.enter_context(patch.object(normal, "stop_process", side_effect=on_stop))
        return spawned, stopped

    def execute_worker(self):
        normal.worker(self.run, self.block["block_id"], self.cell, "1")
        return read(self.folder / "result.json")

    def prepare_recovery(self):
        settings = yaml.safe_load((HERE / "spec.yml").read_text())
        settings["normal"].update(
            blocks=1,
            blocks_per_wave=1,
            max_parallel=1,
            start_deadline="2099-01-01T00:00:00+00:00",
            data_deadline="2099-01-02T00:00:00+00:00",
        )
        dump(self.run / "spec.yml", settings)
        for cell in self.block["conditions"]:
            if cell != self.cell:
                write(
                    self.run / "attempts" / f"{self.block['block_id']}-{cell}" / "1/result.json",
                    {"status": "complete"},
                )
        return settings["normal"]

    def test_audit_rejects_failed_or_unconfirmed_generation_but_keeps_poor_answers(self):
        for error in ("RateLimitError", "BadRequestError", "APIConnectionError", "APITimeoutError"):
            with self.subTest(error=error):
                self.logs(error)
                reasons = normal.audit_game(self.game, self.block["roster"], self.folder / "logs", self.cell)
                self.assertEqual(len(reasons), 5)
                self.assertTrue(all("generation_failed" in reason for reason in reasons))
        self.logs()
        self.assertEqual(normal.audit_game(self.game, self.block["roster"], self.folder / "logs", self.cell), [])
        path = self.folder / "logs" / TEAMS[0] / "llm_calls.jsonl"
        call = json.loads(path.read_text())
        call.pop("success")
        path.write_text(json.dumps(call) + "\n")
        self.assertIn(
            f"{TEAMS[0]}:generation_failed:unsuccessful_call",
            normal.audit_game(self.game, self.block["roster"], self.folder / "logs", self.cell),
        )

    def test_live_monitor_ignores_only_unfinished_last_record(self):
        self.logs()
        path = self.folder / "logs" / TEAMS[0] / "llm_calls.jsonl"
        with path.open("a") as stream:
            stream.write('{"success": fal')
        self.assertEqual(normal.provider_failures(self.folder / "logs", live=True), [])
        self.assertEqual(normal.provider_failures(self.folder / "logs"), [f"{TEAMS[0]}:invalid_call_log"])
        with path.open("a") as stream:
            stream.write('se, "error_type": "RateLimitError"}\n')
        self.assertEqual(
            normal.provider_failures(self.folder / "logs", live=True),
            [f"{TEAMS[0]}:generation_failed:RateLimitError"],
        )

    def test_worker_stops_on_generation_failure_with_or_without_terminal_game(self):
        self.worker_mocks()
        for finished in (False, True):
            with self.subTest(finished=finished):
                for path in normal.critical_markers(self.run):
                    path.unlink()
                if finished:
                    self.publish_game()
                self.logs("RateLimitError")
                result = self.execute_worker()
                self.assertEqual(result["status"], "critical")
                self.assertEqual(len(normal.critical_markers(self.run)), 1)
                self.assertEqual(self.elapsed, 0, "failed generation waited for normal game completion")

    def test_final_json_written_after_clients_exit_is_kept(self):
        self.logs()
        self.worker_mocks(on_client_poll=self.publish_game)
        result = self.execute_worker()
        self.assertEqual(result["status"], "complete")
        self.assertLessEqual(self.elapsed, 2)
        self.assertEqual(normal.row_result(self.run, self.folder.parent.name)["game_path"], result["game_path"])

    def test_shutdown_flush_is_rechecked_without_extending_lease(self):
        self.logs()
        lease = read(self.folder / "start.json")
        lease["timeout"] = 21
        write(self.folder / "start.json", lease)

        def stop(process, timeout):
            if process.is_server:
                self.publish_game()

        self.worker_mocks(on_stop=stop)
        self.assertEqual(self.execute_worker()["status"], "complete")
        self.assertLessEqual(self.elapsed, 1, "client flush grace consumed reserved cleanup time")

    def test_critical_marker_prevents_another_worker_launch(self):
        normal.signal_critical(self.run, self.folder, ["peer:failed"])
        spawned, _ = self.worker_mocks()
        self.assertEqual(self.execute_worker()["status"], "missing")
        spawned.assert_not_called()

    def test_peer_failure_keeps_game_that_finishes_during_cleanup(self):
        self.logs()

        def peer_failure():
            write(self.run / "attempts/_critical/peer.json", {"reasons": ["peer:failed"]})

        def stop(process, timeout):
            if process.is_server:
                self.publish_game()

        self.worker_mocks(on_client_poll=peer_failure, on_stop=stop)
        self.assertEqual(self.execute_worker()["status"], "complete")

    def test_resume_recovers_terminal_json_without_regeneration(self):
        self.prepare_recovery()
        self.logs()
        self.publish_game()
        self.worker_mocks()
        with patch.object(normal, "child") as child_call:
            normal.run(self.run, execute=True)
            child_call.assert_not_called()
        result = read(self.folder / "result.json")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["recovered"])
        self.assertTrue(read(self.run / "state.json")["schedule_finished"])

    def test_resume_after_deadline_recovers_game_published_before_deadline(self):
        self.prepare_recovery()
        settings = yaml.safe_load((self.run / "spec.yml").read_text())
        settings["normal"]["data_deadline"] = (self.started + timedelta(seconds=5)).isoformat()
        dump(self.run / "spec.yml", settings)
        self.logs()
        self.publish_game()
        published = self.started + timedelta(seconds=1)
        os.utime(self.folder / "json/g.json", (published.timestamp(), published.timestamp()))
        self.worker_mocks()
        with (
            patch.object(normal, "now", return_value=self.started + timedelta(seconds=10)),
            patch.object(normal, "before", return_value=False),
            patch.object(normal, "child") as child_call,
        ):
            normal.run(self.run, execute=True)
            child_call.assert_not_called()
        self.assertEqual(read(self.folder / "result.json")["status"], "complete")
        self.assertTrue(read(self.run / "state.json")["schedule_finished"])

    def test_existing_critical_stop_still_recovers_paid_terminal_game(self):
        self.prepare_recovery()
        self.logs()
        self.publish_game()
        normal.signal_critical(self.run, self.folder, ["peer:failed"])
        self.worker_mocks()
        with patch.object(normal, "child") as child_call:
            with self.assertRaisesRegex(ValueError, "Critical audit failure"):
                normal.run(self.run, execute=True)
            child_call.assert_not_called()
        self.assertEqual(read(self.folder / "result.json")["status"], "complete")
        self.assertTrue(read(self.run / "state.json")["critical_stop"])

    def test_late_terminal_json_is_missing_and_never_regenerated(self):
        cfg = self.prepare_recovery()
        self.logs()
        self.publish_game()
        published = self.started + timedelta(seconds=901)
        os.utime(self.folder / "json/g.json", (published.timestamp(), published.timestamp()))
        self.worker_mocks()
        with patch.object(normal, "child") as child_call:
            normal.run(self.run, execute=True)
            normal.run(self.run, execute=True)
            child_call.assert_not_called()
        result = normal.row_result(self.run, self.folder.parent.name)
        self.assertEqual(result["status"], "missing")
        self.assertTrue(result["terminal_game"])
        self.assertFalse(read(self.run / "state.json")["schedule_finished"])

        # The data deadline independently constrains otherwise timely attempts.
        (self.folder / "result.json").unlink()
        cfg["data_deadline"] = (self.started + timedelta(seconds=5)).isoformat()
        published = self.started + timedelta(seconds=10)
        os.utime(self.folder / "json/g.json", (published.timestamp(), published.timestamp()))
        recovered = normal.recover_attempt(self.run, self.block, self.cell, self.folder, cfg)
        self.assertEqual(recovered["status"], "missing")
        self.assertTrue(recovered["terminal_game"])

    def test_existing_completed_result_is_preserved_during_recovery(self):
        cfg = self.prepare_recovery()
        existing = {"status": "complete", "game_path": "already_audited", "game_sha256": "original"}
        write(self.folder / "result.json", existing)
        self.assertEqual(normal.recover_attempt(self.run, self.block, self.cell, self.folder, cfg), existing)

    def test_parent_recovers_terminal_json_after_worker_exit_without_result(self):
        self.prepare_recovery()
        (self.folder / "start.json").unlink()
        self.worker_mocks()

        def child(command, output, timeout):
            self.logs()
            self.publish_game()
            return 124

        with patch.object(normal, "child", side_effect=child) as child_call:
            normal.run(self.run, execute=True)
            self.assertEqual(child_call.call_count, 1)
        self.assertEqual(read(self.folder / "result.json")["status"], "complete")
        self.assertTrue(read(self.run / "state.json")["schedule_finished"])

    def test_failed_game_stops_scheduler_without_retry_or_another_block(self):
        settings = yaml.safe_load((HERE / "spec.yml").read_text())
        settings["normal"].update(
            blocks=2,
            blocks_per_wave=1,
            max_parallel=1,
            start_deadline="2099-01-01T00:00:00+00:00",
            data_deadline="2099-01-02T00:00:00+00:00",
        )
        dump(self.run / "spec.yml", settings)
        second = {**self.block, "block_id": "B002"}
        write(self.run / "schedule.json", [self.block, second])
        write(self.run / "state.json", {"started": [], "schedule_finished": False})
        # The fixture's pre-existing attempt belongs to an unrelated cell.
        (self.folder / "start.json").unlink()
        self.worker_mocks()

        def child(command, output, timeout):
            block_id, cell, attempt = command[-4:-1]
            self.logs("RateLimitError", folder=output.parent, cell=cell)
            normal.worker(self.run, block_id, cell, attempt)
            return 0

        with patch.object(normal, "child", side_effect=child) as child_call:
            with self.assertRaisesRegex(RuntimeError, "Critical audit failure"):
                normal.run(self.run, execute=True)
            self.assertEqual(child_call.call_count, 1)
            state = read(self.run / "state.json")
            self.assertTrue(state["critical_stop"])
            self.assertFalse(state["schedule_finished"])
            self.assertEqual(state["started"], [self.block["block_id"]])
            with self.assertRaisesRegex(ValueError, "Critical audit failure"):
                normal.run(self.run, execute=True)
            self.assertEqual(child_call.call_count, 1)

    def test_resilient_worker_isolates_transport_failure_and_uses_single_client(self):
        self.prepare_recovery()
        settings = yaml.safe_load((self.run / "spec.yml").read_text())
        settings["normal"]["resilient"] = True
        dump(self.run / "spec.yml", settings)
        for path in (self.run / "cells").glob("*/*.yml"):
            cfg = yaml.safe_load(path.read_text())
            cfg["openai"] = {}
            dump(path, cfg)
        self.logs("APIConnectionError")
        spawned, _ = self.worker_mocks()
        result = self.execute_worker()
        self.assertEqual(result["status"], "retryable")
        self.assertFalse(normal.critical_markers(self.run))
        for call in spawned.call_args_list[1:]:
            self.assertIn("experiments.pp.revision.client", call.args[0])
        cfg = yaml.safe_load((self.folder / f"{TEAMS[0]}.yml").read_text())
        self.assertTrue(cfg["experiment"]["request_audit"])
        self.assertEqual(cfg["openai"]["max_retries"], 1)
        self.assertEqual(cfg["openai"]["connect_timeout_seconds"], 5)

    def test_completed_server_game_with_pending_request_is_not_accepted(self):
        self.logs()
        self.publish_game()
        write(self.folder / "logs" / TEAMS[0] / "request_audit" / "pending.json", {"status": "pending"})
        self.assertIn(
            f"{TEAMS[0]}:unfinished_request",
            normal.audit_game(self.game, self.block["roster"], self.folder / "logs", self.cell),
        )


if __name__ == "__main__":
    unittest.main()
