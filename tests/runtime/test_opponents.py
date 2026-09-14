"""Unit tests for the deterministic, API-free Stage 0 opponent."""

# ruff: noqa: PT009

from __future__ import annotations

from unittest import TestCase

from aiwolf_nlp_common.packet import Info, Judge, Request, Role, Species, Status

from experiments.runtime.opponents import RuleOpponent


def info(role: Role, *, day: int = 1, divine_result: Judge | None = None) -> Info:
    """Create a minimal server state with self and dead targets."""
    return Info(
        game_id="01JTESTGAME0000000000000000",
        day=day,
        agent="self",
        profile=None,
        medium_result=None,
        divine_result=divine_result,
        executed_agent=None,
        attacked_agent=None,
        vote_list=None,
        attack_vote_list=None,
        status_map={"self": Status.ALIVE, "alice": Status.ALIVE, "bob": Status.DEAD, "carol": Status.ALIVE},
        role_map={"self": role},
    )


class RuleOpponentTest(TestCase):
    """Verify target legality, determinism, and request-specific responses."""

    def policy(self, role: Role, *, pool: str = "A", divine_result: Judge | None = None) -> RuleOpponent:
        """Return a policy initialized with a fixed game state."""
        policy = RuleOpponent(pool, "pilotAalpha1")
        policy.info = info(role, divine_result=divine_result)
        policy.role = role
        return policy

    def test_target_is_deterministic_and_avoids_self_and_dead_agents(self) -> None:
        """The same identity, game and salt always select the same legal target."""
        first = self.policy(Role.VILLAGER)
        second = self.policy(Role.VILLAGER)
        targets = {first.target(salt) for salt in ("talk", "vote", "divine", "guard", "attack")}
        self.assertEqual(first.target("vote"), second.target("vote"))
        self.assertTrue(targets <= {"alice", "carol"})
        self.assertNotIn("self", targets)
        self.assertNotIn("bob", targets)

    def test_role_specific_talk(self) -> None:
        """Seer and possessed claims differ while all roles return legal text."""
        result = Judge(day=0, agent="self", target="alice", result=Species.HUMAN)
        seer = self.policy(Role.SEER, divine_result=result)
        possessed = self.policy(Role.POSSESSED)
        self.assertEqual(seer.talk(), "I am the seer. alice was HUMAN.")
        self.assertRegex(possessed.talk(), r"^I am the seer\. (alice|carol) was HUMAN\.$")
        for role in Role:
            response = self.policy(role).talk()
            self.assertIsInstance(response, str)
            self.assertTrue(response)

    def test_vote_divine_guard_and_attack_return_only_living_others(self) -> None:
        """Every targeted action is deterministic and avoids self/dead targets for every role."""
        for role in Role:
            for request in (Request.VOTE, Request.DIVINE, Request.GUARD, Request.ATTACK):
                first = self.policy(role).respond(request)
                second = self.policy(role).respond(request)
                self.assertEqual(first, second)
                self.assertIn(first, {"alice", "carol"})

    def test_name_talk_and_whisper_responses(self) -> None:
        """Non-targeted response types are stable and request appropriate."""
        policy = self.policy(Role.WEREWOLF, pool="C")
        self.assertEqual(policy.respond(Request.NAME), "pilotAalpha1")
        self.assertTrue(policy.respond(Request.TALK))
        self.assertEqual(policy.respond(Request.WHISPER), "Skip")
