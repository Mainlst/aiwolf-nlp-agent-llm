"""Offline scientific-design and safety regressions for the revision runners."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from experiments.pp.revision.analyze import analyze_b, bounds, interval, talk_success
from experiments.pp.revision.common import HERE, ROOT, TEAMS, before, digest, freeze, read, verify, verify_frozen, write
from experiments.pp.revision.fixed import encode, decode, group_worker, restore, snapshot
from experiments.pp.revision.normal import audit_game, row_result, run as run_b
from experiments.pp.revision.preflight import check
from experiments.pp.revision.prepare import allocation, cell_configs, optimizer, policies, prepare, validate_settings


class RevisionTests(unittest.TestCase):
    def test_prepare_cli_defaults_to_seatfixed(self):
        from experiments.pp.revision.prepare import main

        with (
            patch("sys.argv", ["prepare", "B", "/tmp/revision-cli-test"]),
            patch("experiments.pp.revision.prepare.prepare") as mocked_prepare,
            patch("builtins.print"),
        ):
            main()
        self.assertEqual(
            mocked_prepare.call_args.args[0].server_binary,
            ROOT / "server/aiwolf-nlp-server-linux-amd64-seatfixed",
        )

    def test_commands_pass_same_server_to_smoke_and_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            spy = Path(tmp) / "argument-spy"
            spy.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
            spy.chmod(0o700)
            env = {k: v for k, v in os.environ.items() if not k.startswith("REVISION_")}
            env.update(REVISION_PYTHON=str(spy), REVISION_RUN_ROOT=tmp)
            for custom in (None, str(Path(tmp) / "server with spaces")):
                if custom:
                    env["REVISION_SERVER_BINARY"] = custom
                for command in ("prepare-smoke-b", "prepare-b"):
                    result = subprocess.run(
                        ["bash", str(HERE / "commands.sh"), command],
                        cwd=tmp,
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    args = result.stdout.splitlines()
                    actual = Path(args[args.index("--server-binary") + 1]).resolve()
                    self.assertEqual(
                        actual, Path(custom) if custom else ROOT / "server/aiwolf-nlp-server-linux-amd64-seatfixed"
                    )

    def test_unsupported_specification_fails_before_preparation(self):
        settings = yaml.safe_load((HERE / "spec.yml").read_text())
        validate_settings(settings)
        settings["fixed"]["repetitions"] = 3
        with self.assertRaisesRegex(ValueError, "two-repetition"):
            validate_settings(settings)
        settings["fixed"]["repetitions"] = 2
        settings["normal"]["start_deadline"] = "2026-09-10T12:00:00"
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            validate_settings(settings)

    def test_judge_receives_shared_talk_without_actual_vote(self):
        from experiments.pp.revision.common import jsonl
        from experiments.pp.revision.fixed import JudgeBudget, judge_group

        item = jsonl(ROOT / "experiments/pp/fixed_boards_v2/generated/inputs.jsonl")[0]
        label = jsonl(ROOT / "experiments/pp/fixed_boards_v2/generated/labels.jsonl")[0]
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            settings = yaml.safe_load((HERE / "spec.yml").read_text())
            settings["fixed"]["data_deadline"] = "2099-01-01T00:00:00+00:00"
            (run / "spec.yml").write_text(yaml.safe_dump(settings))
            (run / "inputs.jsonl").write_text(json.dumps(item) + "\n")
            (run / "labels.jsonl").write_text(json.dumps(label) + "\n")
            (run / "judge-spec").mkdir()
            for name in ("config.yml", "rubric.md"):
                (run / "judge-spec" / name).write_text((ROOT / "experiments/pp/fixed_boards/judge" / name).read_text())
            group = {"group_id": "G", "scenario_id": item["scenario_id"], "talk_bit": "1"}
            unit = {
                "unit_id": "G_A10",
                "game_id": "test",
                "scenario_id": item["scenario_id"],
                "condition": "A10",
                "repetition": 1,
                "input_sha256": item["input_sha256"],
                "talk": "I am possessed. Let's match votes.",
                "vote": label["correct_ally"],
            }
            write(run / "units/G_A10.json", unit)
            cfg = yaml.safe_load((run / "judge-spec/config.yml").read_text())
            budget = JudgeBudget(run, cfg)

            def fake_judge(client, inputs, config, instructions):
                self.assertNotIn("target_vote", inputs)
                self.assertNotIn("condition", inputs)
                self.assertIn("intentionally absent", instructions)
                return {
                    "success": True,
                    "response_model_id": cfg["model"],
                    "usage": {},
                    "result": {
                        k: {
                            "label": True,
                            "evidence": [{"event_id": "target-talk", "quote": "I am possessed."}],
                            "reason": "Test classifier evidence from the shared TALK.",
                        }
                        for k in ("pp_recognized", "ally_recognized", "vote_coordination")
                    },
                }

            with patch("experiments.pp.fixed_boards.pp_judge.call_judge", side_effect=fake_judge) as call:
                judge_group(run, group, object(), budget)
                judge_group(run, group, object(), budget)
                self.assertEqual(call.call_count, 3)
            budget.reserve()
            self.assertTrue(budget.finish({"error": {"type": "AuthenticationError"}, "usage": {}}))
            self.assertFalse(budget.reserve())

    def test_analysis_a_applies_two_repetitions_and_clusters_role_views(self):
        from experiments.pp.revision.analyze import analyze_a
        from experiments.pp.revision.common import jsonl

        source = jsonl(ROOT / "experiments/pp/fixed_boards_v2/generated/inputs.jsonl")
        labels = jsonl(ROOT / "experiments/pp/fixed_boards_v2/generated/labels.jsonl")
        ids = {"FV2-E-POS-001", "FV2-E-WER-001"}
        items = [i for i in source if i["scenario_id"] in ids]
        selected_labels = {l["scenario_id"]: l for l in labels if l["scenario_id"] in ids}
        units, groups = [], []
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "spec.yml").write_text((HERE / "spec.yml").read_text())
            (run / "inputs.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items))
            (run / "labels.jsonl").write_text("".join(json.dumps(l) + "\n" for l in selected_labels.values()))
            for item in items:
                label = selected_labels[item["scenario_id"]]
                target = label["valid_village_targets"][0]
                role = item["input"]["perspective"]["role"].lower()
                talk = f"I am the {role}. PP is available. I vote {target}. Let's match votes."
                for repetition in (1, 2):
                    for bit in ("0", "1"):
                        group_id = f"{item['scenario_id']}-{bit}-{repetition}"
                        groups.append({"group_id": group_id})
                        for judge_r in (1, 2, 3):
                            write(
                                run / "judge" / group_id / f"R{judge_r}.json",
                                {
                                    "success": True,
                                    "result": {
                                        k: {"label": judge_r != 3}
                                        for k in ("pp_recognized", "ally_recognized", "vote_coordination")
                                    },
                                },
                            )
                        for vote_bit in ("0", "1"):
                            condition = f"A{bit}{vote_bit}"
                            units.append(
                                {
                                    "unit_id": f"{group_id}-{vote_bit}",
                                    "group_id": group_id,
                                    "scenario_id": item["scenario_id"],
                                    "condition": condition,
                                    "repetition": repetition,
                                    "input_sha256": item["input_sha256"],
                                    "talk": talk,
                                    "vote": label["correct_ally"] if condition == "A10" and repetition == 2 else target,
                                }
                            )
            write(run / "schedule.json", groups)
            with patch("experiments.pp.revision.analyze.all_units", return_value=units):
                result = analyze_a(run, {})
            self.assertEqual(result["primary"]["clusters"], 1)
            self.assertEqual(result["primary"]["difference"], 1)
            self.assertEqual(result["rates"]["A10"]["talk_success_vote_failure"]["numerator"], 2)
            self.assertEqual(result["rates"]["A10"]["consistent"]["denominator"], 2)

    def test_b_scheduler_retries_once_and_resume_does_not_regenerate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            settings = yaml.safe_load((HERE / "spec.yml").read_text())
            settings["normal"].update(
                blocks=2, start_deadline="2099-01-01T00:00:00+00:00", data_deadline="2099-01-02T00:00:00+00:00"
            )
            (run / "spec.yml").write_text(yaml.safe_dump(settings))
            schedule = allocation(2, 1)
            write(run / "schedule.json", schedule)
            write(run / "state.json", {"started": [], "schedule_finished": False})
            calls = []
            fail_row = f"{schedule[0]['block_id']}-{schedule[0]['conditions'][0]}"

            def fake_child(command, output, timeout):
                folder = output.parent
                calls.append(str(folder))
                self.assertEqual(len(read(run / "state.json")["started"]), 2)
                status = "missing" if folder.parent.name == fail_row and folder.name == "1" else "complete"
                write(folder / "result.json", {"status": status})
                return 0

            with (
                patch("experiments.pp.revision.normal.verify", return_value={"kind": "B"}),
                patch("experiments.pp.revision.normal.load_credentials") as credentials,
                patch("experiments.pp.revision.normal.child", side_effect=fake_child),
            ):
                run_b(run)
                credentials.assert_not_called()
                self.assertEqual(calls, [])
                run_b(run, execute=True)
                self.assertEqual(len(calls), 9)
                self.assertTrue(read(run / "state.json")["schedule_finished"])
                run_b(run, execute=True)
                self.assertEqual(len(calls), 9)

    def test_b_scheduler_does_not_register_after_start_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            settings = yaml.safe_load((HERE / "spec.yml").read_text())
            settings["normal"]["start_deadline"] = "2000-01-01T00:00:00+00:00"
            (run / "spec.yml").write_text(yaml.safe_dump(settings))
            write(run / "schedule.json", allocation(2, 1))
            write(run / "state.json", {"started": [], "schedule_finished": False})
            with (
                patch("experiments.pp.revision.normal.verify", return_value={"kind": "B"}),
                patch("experiments.pp.revision.normal.load_credentials"),
                patch("experiments.pp.revision.normal.child") as child,
            ):
                run_b(run, execute=True)
                child.assert_not_called()
            self.assertEqual(read(run / "state.json")["started"], [])

    def test_b_analysis_keeps_partial_outcomes_only_for_missing_bounds(self):
        from experiments.pp.revision.common import digest

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            settings = yaml.safe_load((HERE / "spec.yml").read_text())
            settings["analysis"]["bootstrap_iterations"] = 20
            (run / "spec.yml").write_text(yaml.safe_dump(settings))
            blocks = allocation(3, 1)
            write(run / "schedule.json", blocks)
            for index, block in enumerate(blocks):
                for cell in ("G00", "G10", "G01", "G11"):
                    if index == 2 and cell != "G11":
                        continue
                    won = (index == 0 and cell == "G11") or (index == 1 and cell == "G00") or index == 2
                    folder = run / "attempts" / f"{block['block_id']}-{cell}" / "1"
                    game = {
                        "game_id": f"{index}-{cell}",
                        "win_side": "WEREWOLF" if won else "VILLAGER",
                        "entries": [
                            {
                                "request": json.dumps(
                                    {
                                        "request": "FINISH",
                                        "info": {
                                            "role_map": {"Wolf": "WEREWOLF", "Pos": "POSSESSED", "Vill": "VILLAGER"}
                                        },
                                    }
                                )
                            }
                        ],
                    }
                    write(folder / "game.json", game)
                    write(
                        folder / "result.json",
                        {
                            "status": "complete",
                            "game_path": str((folder / "game.json").relative_to(run)),
                            "game_sha256": digest(folder / "game.json"),
                        },
                    )
            result = analyze_b(run, {"state": {"started": [b["block_id"] for b in blocks]}})
            self.assertEqual(result["complete_blocks"], 2)
            self.assertEqual(result["incomplete_blocks"], 1)
            self.assertEqual(result["primary"]["difference"], 0)
            self.assertEqual(result["rates_complete_blocks"]["G11"]["denominator"], 2)
            self.assertEqual(result["missing_outcome_bounds_not_ci"], [0, 1 / 3])

    def test_complete_factorial_balanced_seats_and_optimizer(self):
        blocks = allocation(200, 20260907)
        self.assertEqual(blocks, allocation(200, 20260907))
        self.assertEqual(len({b["block_id"] for b in blocks}), 200)
        seats = Counter()
        for block in blocks:
            self.assertEqual(set(block["conditions"]), {"G00", "G10", "G01", "G11"})
            roster = block["roster"]
            seats[(roster[0]["seat"], roster[1]["seat"])] += 1
            seed = optimizer(roster)
            for row in roster:
                self.assertEqual(seed["idx_team_map"][str(row["seat"] - 1)], row["team"])
                self.assertIn(row["seat"] - 1, seed["scheduled_matches"][0]["role_idxs"][row["role"]])
        self.assertEqual(set(seats.values()), {10})

    def test_only_two_roles_change(self):
        configs = policies()
        baseline = cell_configs(configs, "G00")
        for cell in ("G10", "G01", "G11"):
            candidate = cell_configs(configs, cell)
            for team in TEAMS[2:]:
                self.assertEqual(candidate[team], baseline[team])
            for i in (0, 1):
                self.assertEqual(candidate[TEAMS[i]]["experiment"]["pp_instruction_enabled"], cell[i + 1] == "1")
        changed = cell_configs(configs, "G11")
        changed[TEAMS[2]]["strategy"]["VILLAGER"] = "mutation"
        self.assertNotEqual(changed[TEAMS[2]], baseline[TEAMS[2]])

    def test_deadline_and_missing_bounds(self):
        deadline = "2026-09-10T03:00:00+00:00"
        self.assertTrue(before(deadline, datetime(2026, 9, 10, 2, tzinfo=timezone.utc)))
        self.assertFalse(before(deadline, datetime(2026, 9, 10, 3, tzinfo=timezone.utc)))
        self.assertEqual(bounds([{"G11": True, "G00": False}, {"G11": None, "G00": True}]), [0, 0.5])
        self.assertEqual(bounds([{"G11": None, "G00": None}]), [-1, 1])
        self.assertIsNone(interval([[1]], 20)["ci95"])
        self.assertIsNone(interval([], 20)["difference"])

    def test_completed_attempt_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            write(run / "attempts/B001-G00/1/result.json", {"status": "complete", "game_path": "first"})
            write(run / "attempts/B001-G00/2/result.json", {"status": "complete", "game_path": "second"})
            self.assertEqual(row_result(run, "B001-G00")["game_path"], "first")

    def test_state_restoration_does_not_share_history_or_model(self):
        import asyncio
        from types import SimpleNamespace
        from aiwolf_nlp_common.packet import Request, Role, Talk

        source = SimpleNamespace(
            config={"secret": "excluded"},
            llm_model=object(),
            agent_logger=object(),
            talk_phase_event=asyncio.Event(),
            whisper_phase_event=asyncio.Event(),
            role=Role.WEREWOLF,
            request=Request.TALK,
            talk_history=[Talk(1, 2, 1, "Alice", "vote Bob")],
            sent_talk_count=1,
        )
        state = snapshot(source)
        self.assertNotIn("secret", json.dumps(state))
        branch = deepcopy(source)
        own_model = branch.llm_model
        restore(branch, state)
        self.assertIs(branch.llm_model, own_model)
        branch.talk_history[0].text = "changed"
        branch.sent_talk_count += 1
        self.assertEqual(source.talk_history[0].text, "vote Bob")
        restore(branch, state)
        self.assertEqual(snapshot(branch), state)
        self.assertEqual(decode(encode(source.role)), Role.WEREWOLF)

    def test_diagnostic_includes_correct_talk_then_ally_vote(self):
        score = {
            "talk_declared_ally_preserved": True,
            "semantic": {"pp_recognized": True, "vote_coordination": True},
            "deterministic": {
                "correct_co_or_response": True,
                "valid_target_selected": True,
                "ally_preserved": False,
                "talk_vote_consistent": False,
            },
        }
        self.assertTrue(talk_success(score))

    def test_a_shared_talk_survives_resume_and_vote_mutations(self):
        """Real Agent methods and logger, fake LLM: no provider client is created."""
        import sys

        sys.path.insert(0, str(ROOT / "src"))
        from langchain_core.messages import AIMessage
        from agent.agent import Agent
        from experiments.pp.fixed_boards.run_confirmation import read_jsonl

        item = read_jsonl(ROOT / "experiments/pp/fixed_boards_v2/generated/inputs.jsonl")[0]
        names = [n for n, v in item["input"]["snapshot"]["status_map"].items() if v == "ALIVE"]
        own = item["input"]["perspective"]["agent"]
        target = next(n for n in names if n != own)
        observed = []

        class FakeModel:
            temperature = 1.0

            def invoke(self, messages):
                prompt = str(messages[0].content)
                observed.append(prompt)
                return AIMessage(
                    content=target,
                    usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    response_metadata={"model_name": "gpt-5.6-luna"},
                )

        def initialize(agent):
            agent.llm_model = FakeModel()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(Agent, "initialize", initialize),
            patch("experiments.pp.revision.fixed.verify"),
            patch("experiments.pp.revision.fixed.load_credentials"),
            patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
        ):
            run = Path(tmp)
            settings = yaml.safe_load((HERE / "spec.yml").read_text())
            settings["fixed"]["data_deadline"] = "2099-01-01T00:00:00+00:00"
            (run / "spec.yml").write_text(yaml.safe_dump(settings))
            (run / "inputs.jsonl").write_text(json.dumps(item) + "\n")
            group = {
                "group_id": "shared",
                "scenario_id": item["scenario_id"],
                "talk_bit": "1",
                "repetition": 1,
                "vote_bits": ["1", "0"],
            }
            write(run / "schedule.json", [group])
            write(run / "state.json", {"started": ["shared"]})
            for policy, config in policies().items():
                (run / "policies").mkdir(exist_ok=True)
                (run / "policies" / f"{policy}.yml").write_text(yaml.safe_dump(config))
            group_worker(run, "shared")
            self.assertEqual(len(observed), 3)
            first = read(run / "units/shared_A11.json")
            second = read(run / "units/shared_A10.json")
            self.assertEqual(first["talk"], second["talk"])
            self.assertEqual(first["branch_state_sha256"], second["branch_state_sha256"])
            self.assertNotEqual(observed[1], observed[2])
            group_worker(run, "shared")
            self.assertEqual(len(observed), 3, "resume regenerated a successful request")

    def test_prepare_and_preflight_are_offline_and_detect_drift(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
        ):
            binary = Path(tmp) / "server"
            binary.write_text("fake binary: never execute")
            run = Path(tmp) / "b"
            args = argparse.Namespace(
                run_dir=run,
                kind="B",
                purpose="smoke",
                spec=HERE / "spec.yml",
                server_config=ROOT / "config/server.example.yml",
                server_binary=binary,
                b_run=None,
            )
            with self.assertRaisesRegex(ValueError, "Server binary SHA-256 mismatch"):
                prepare(args)
            self.assertFalse(run.exists(), "Rejected binary left a prepared run behind")
            # A test-only spec permits the inert fixture; production keeps the original hash.
            settings = yaml.safe_load(args.spec.read_text())
            settings["normal"]["required_server_binary_sha256"] = digest(binary)
            args.spec = Path(tmp) / "test-spec.yml"
            args.spec.write_text(yaml.safe_dump(settings))
            prepare(args)
            self.assertEqual(check(run)["planned_games_or_generation_calls"], 8)
            self.assertFalse(list(run.rglob(".env")))
            original = binary.read_bytes()
            binary.write_bytes(b"replaced binary")
            with self.assertRaisesRegex(ValueError, "Server binary changed"):
                verify(run)
            binary.write_bytes(original)
            manifest = read(run / "manifest.json")
            write(run / "manifest.json", {**manifest, "server_binary_sha256": "0" * 64})
            with self.assertRaisesRegex(ValueError, "required seatfixed SHA-256"):
                verify(run)
            write(run / "manifest.json", manifest)
            state = read(run / "state.json")
            state["schedule_finished"] = True
            write(run / "state.json", state)
            freeze(run)
            verify_frozen(run)
            write(run / "attempts/extra/result.json", {"status": "complete"})
            with self.assertRaisesRegex(ValueError, "added or removed"):
                verify_frozen(run)
            (run / "policies/P+.yml").write_text("changed")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                verify(run)

    def test_game_audit_rejects_partner_role_or_policy_mismatch(self):
        block = allocation(1, 1)[0]
        game = {
            "game_id": "g",
            "agents": [{"idx": r["seat"], "team": r["team"], "role": r["role"]} for r in block["roster"]],
        }
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            for index, team in enumerate(TEAMS):
                (logs / team).mkdir()
                call = {
                    "game_id": "g",
                    "success": True,
                    "condition": "P+" if index < 2 else "",
                    "model": {"model": "gpt-5.6-luna", "temperature": "1.0", "effective_temperature": "1.0"},
                }
                (logs / team / "llm_calls.jsonl").write_text(json.dumps(call) + "\n")
            self.assertEqual(audit_game(game, block["roster"], logs, "G11"), [])
            game["agents"][1]["role"] = "WEREWOLF"
            self.assertIn("full_roster_mismatch", audit_game(game, block["roster"], logs, "G11"))
            self.assertTrue(any("policy_mismatch" in r for r in audit_game(game, block["roster"], logs, "G00")))


if __name__ == "__main__":
    unittest.main()
