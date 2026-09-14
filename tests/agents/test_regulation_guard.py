"""Tests for the INLG 2026 output regulation guard."""

# ruff: noqa: PT009, SLF001

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from aiwolf_nlp_common.packet import Info, Request, Role, Status

from utils.agent_utils import Agent


class RegulationGuardTest(TestCase):
    """Verify that text is made safe without another model invocation."""

    def setUp(self) -> None:
        """Create a minimal agent state backed by server-like settings."""
        self.agent = object.__new__(Agent)
        self.agent.config = {"agent": {"guard": {"enabled": True, "language": "en"}}}
        self.agent.agent_name = "Daisuke"
        self.agent.agent_logger = SimpleNamespace(logger=Mock())
        self.agent.info = Info(
            game_id="test-game",
            day=1,
            agent="Daisuke",
            profile=None,
            medium_result=None,
            divine_result=None,
            executed_agent=None,
            attacked_agent=None,
            vote_list=None,
            attack_vote_list=None,
            status_map={
                "Daisuke": Status.ALIVE,
                "Alice": Status.ALIVE,
                "Bob": Status.ALIVE,
            },
            role_map={"Daisuke": Role.VILLAGER},
        )
        max_length = SimpleNamespace(base_length=10, mention_length=8)
        whisper_max_length = SimpleNamespace(base_length=4, mention_length=3)
        max_count = SimpleNamespace(per_agent=4)
        self.agent.setting = SimpleNamespace(
            talk=SimpleNamespace(max_length=max_length, max_count=max_count),
            whisper=SimpleNamespace(max_length=whisper_max_length, max_count=max_count),
        )
        self.agent.request = Request.TALK
        self.agent.freeform_talk_send_count = 0
        self.agent.freeform_whisper_send_count = 0

    def test_removes_half_width_comma_and_unsupported_symbols(self) -> None:
        """Forbidden punctuation and emoji must never reach the server."""
        result = self.agent._sanitize_freeform_text("Alice, I agree 😀")

        self.assertNotIn(",", result)
        self.assertNotIn("😀", result)
        self.assertTrue(result.isascii())
        self.assertLessEqual(self.agent._count_non_space_characters(result), 10)

    def test_non_english_letters_fall_back_to_skip(self) -> None:
        """A non-English utterance is safer to skip than partially transmit."""
        result = self.agent._sanitize_freeform_text("Alice is suspicious でも慎重に")

        self.assertEqual(result, "Skip")

    def test_protocol_command_falls_back_to_skip(self) -> None:
        """Protocol syntax is forbidden in natural-language talk."""
        result = self.agent._sanitize_freeform_text("VOTE Alice")

        self.assertEqual(result, "Skip")
        self.assertIn(
            "PROTOCOL_OUTPUT",
            self.agent._detect_violations(Request.TALK, "VOTE Alice"),
        )

    def test_invalid_mention_is_removed(self) -> None:
        """Only a name supplied in status_map may be used as an anchor."""
        result = self.agent._sanitize_freeform_text("@Ghost explain")

        self.assertEqual(result, "explain")
        self.assertNotIn("@Ghost", result)

    def test_valid_mention_uses_independent_space_excluding_limits(self) -> None:
        """The server gives separate limits to text before and after a mention."""
        result = self.agent._sanitize_freeform_text("a a a a a a a a a a @Alice b b b b b b b b")

        before, after = result.split("@Alice")
        self.assertEqual(self.agent._count_non_space_characters(before), 10)
        self.assertEqual(self.agent._count_non_space_characters(after), 8)

    def test_explicit_request_selects_whisper_limits(self) -> None:
        """Realtime packet updates must not make whisper use talk limits."""
        result = self.agent._sanitize_freeform_text("a b c d e f", Request.WHISPER)

        self.assertEqual(self.agent._count_non_space_characters(result), 4)

    def test_detects_regulation_violations_in_raw_output(self) -> None:
        """Violation logs should identify each corrected rule."""
        self.assertIn(
            "HALF_WIDTH_COMMA",
            self.agent._detect_violations(Request.TALK, "Alice, explain"),
        )
        self.assertIn(
            "NON_ENGLISH_TEXT",
            self.agent._detect_violations(Request.TALK, "アリスを疑う"),
        )
        self.assertIn(
            "INVALID_PLAYER_MENTION",
            self.agent._detect_violations(Request.TALK, "@Ghost explain"),
        )
        self.assertNotIn(
            "LENGTH_EXCEEDED",
            self.agent._detect_violations(Request.TALK, "a a a a a a a a a a"),
        )
        self.assertIn(
            "LENGTH_EXCEEDED",
            self.agent._detect_violations(Request.TALK, "a a a a a a a a a a a"),
        )

    def test_local_freeform_counter_prevents_a_fifth_talk(self) -> None:
        """Broadcast latency must not permit more than four local sends."""
        for _ in range(4):
            self.assertTrue(self.agent._can_send_freeform(Request.TALK))
            self.agent._record_freeform_send(Request.TALK)

        self.assertFalse(self.agent._can_send_freeform(Request.TALK))

    def test_freeform_converts_internal_skip_to_over(self) -> None:
        """The realtime track forbids Skip and uses Over to end speech."""
        self.assertEqual(self.agent._freeform_wire_text("Skip"), "Over")
        self.assertEqual(self.agent._freeform_wire_text("Hello"), "Hello")
