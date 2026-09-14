"""Tests for preregistered F/C/R/G experiment controls."""

# ruff: noqa: PT009, SLF001

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from aiwolf_nlp_common.packet import Info, Request, Role, Status

from utils.agent_utils import Agent


class ExperimentControlsTest(TestCase):
    """Ensure each ablation switch changes only its intended mechanism."""

    def setUp(self) -> None:
        """Build a minimal agent state without initializing an LLM."""
        self.agent = object.__new__(Agent)
        self.agent.config = {
            "agent": {"guard": {"enabled": True, "language": "en", "audit_all_outputs": True}},
            "experiment": {"condition": "F", "dynamic_context_enabled": True, "role_strategy_enabled": True},
            "strategy": {"VILLAGER": "role-specific"},
        }
        self.agent.agent_name = "Alice"
        self.agent.llm_model = None
        self.agent.role = Role.VILLAGER
        self.agent.profile = None
        self.agent.agent_logger = SimpleNamespace(logger=Mock(), output_dir=SimpleNamespace())
        self.agent.info = Info(
            game_id="test-game",
            day=1,
            agent="Alice",
            profile=None,
            medium_result=None,
            divine_result=None,
            executed_agent=None,
            attacked_agent=None,
            vote_list=None,
            attack_vote_list=None,
            status_map={"Alice": Status.ALIVE, "Bob": Status.ALIVE},
            role_map={"Alice": Role.VILLAGER},
        )
        length = SimpleNamespace(base_length=125, mention_length=125)
        self.agent.setting = SimpleNamespace(
            talk=SimpleNamespace(max_length=length),
            whisper=SimpleNamespace(max_length=length),
            vote=SimpleNamespace(allow_self_vote=False),
        )

    def test_openai_temperature_mismatch_fails_closed(self) -> None:
        """SDK parameter removal must stop a run before the first API call."""
        self.agent.config.update(
            {"llm": {"type": "openai"}, "openai": {"model": "gpt-5.6-luna", "temperature": 0.7}},
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), self.assertRaisesRegex(
            ValueError,
            "configured=0.7, effective=None",
        ):
            self.agent.initialize()

    def test_openai_effective_temperature_is_logged(self) -> None:
        """Frozen Luna temperature must survive SDK model construction unchanged."""
        self.agent.config.update(
            {"llm": {"type": "openai"}, "openai": {"model": "gpt-5.6-luna", "temperature": 1.0}},
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            self.agent.initialize()
        self.assertEqual(self.agent._llm_debug_model_config()["effective_temperature"], "1.0")

    def test_role_strategy_ablation_returns_shared_generic_text(self) -> None:
        """R must not expose the configured role-specific strategy."""
        self.agent.config["experiment"].update(
            {"role_strategy_enabled": False, "generic_role_strategy": "same generic instruction"},
        )
        self.assertEqual(self.agent._get_role_strategy(), "same generic instruction")

    def test_dynamic_context_ablation_keeps_factual_header(self) -> None:
        """C keeps server facts but omits derived context builders."""
        self.agent.config["experiment"]["dynamic_context_enabled"] = False
        self.agent._build_endgame_context = Mock(side_effect=AssertionError("derived builder called"))
        header = self.agent._build_game_context_header(Request.TALK, self.agent.info)
        self.assertIn("[Current game context]", header)
        self.assertTrue(any("role is the villager" in line for line in header))

    def test_pp_ablation_keeps_endgame_context_but_removes_pp_guidance(self) -> None:
        """P-PP retains generic dynamic endgame facts and suppresses PP inserts."""
        self.agent.config["experiment"]["pp_instruction_enabled"] = False
        self.agent.role = Role.WEREWOLF
        self.agent.info.status_map = {
            "Alice": Status.ALIVE,
            "Bob": Status.ALIVE,
            "Carol": Status.ALIVE,
            "Dave": Status.DEAD,
            "Eve": Status.DEAD,
        }
        self.agent._extract_seer_claims = Mock(return_value=set())

        context = self.agent._build_endgame_context(self.agent.info)

        self.assertIn("[Endgame notes]", context)
        self.assertIn("In a 5-player endgame", context)
        self.assertNotIn("PP", context)
        self.assertNotIn("declare werewolf", context)

    def test_pp_guidance_is_enabled_by_default(self) -> None:
        """Production and P+ behavior retain the existing PP dynamic insert."""
        self.agent.role = Role.POSSESSED
        self.agent.info.status_map = {
            "Alice": Status.ALIVE,
            "Bob": Status.ALIVE,
            "Carol": Status.ALIVE,
        }

        context = self.agent._build_endgame_context(self.agent.info)

        self.assertIn("PP board", context)
        self.assertIn("declare possessed", context)

    def test_guard_ablation_passes_raw_output(self) -> None:
        """G detects violations but does not repair the model output."""
        self.agent.config["agent"]["guard"].update({"enabled": False, "disabled_behavior": "raw"})
        self.agent._log_violations = Mock()
        raw = "  VOTE Bob\n"
        self.assertEqual(self.agent._process_output_guard(Request.TALK, raw), raw)
        self.agent._log_violations.assert_called_once()

    def test_all_outputs_are_audited_with_before_after_values(self) -> None:
        """Experiment runs must retain compliant outputs as well as violations."""
        with TemporaryDirectory() as output_dir:
            self.agent.agent_logger.output_dir = Path(output_dir)
            self.agent._process_output_guard(Request.TALK, "Bob seems suspicious.")

            records = [
                json.loads(line)
                for line in (Path(output_dir) / "rule_violations.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["raw_llm_response"], "Bob seems suspicious.")
        self.assertEqual(records[0]["final_response"], "Bob seems suspicious.")
        self.assertTrue(records[0]["guard_enabled"])
        self.assertFalse(records[0]["modified"])
