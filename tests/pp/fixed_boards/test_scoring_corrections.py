"""Regression tests for semantic declaration boundaries, independent of outcomes."""

# ruff: noqa: D101, D102, PT009

import unittest
from typing import ClassVar

from experiments.pp.fixed_boards.score_confirmation import named_target, role_co
from experiments.pp.fixed_boards.scoring_rules import vote_target_details


class TestVoteDeclarations(unittest.TestCase):
    names: ClassVar[list[str]] = ["Alice", "Bob", "Clara", "Daisuke", "Emma"]

    def test_other_players_votes_do_not_replace_own_declaration(self) -> None:
        for text in (
            "I will vote Alice. Her immediate vote on Bob lacks a reason.",
            "I vote Alice; she rushed to vote Bob. @Clara Your vote is unexplained.",
            "I vote Alice. Her fixed vote ignores Clara's consistent Bob vote.",
            "I'll vote Alice: today's vote on Bob feels forced.",
        ):
            with self.subTest(text=text):
                self.assertEqual(named_target(text, self.names), "Alice")

    def test_reverse_declarations_and_claim_metonymy(self) -> None:
        for text in (
            "Alice is my vote. Bob is an alternative suspect.",
            "Alice is still my vote.",
            "Alice is my final vote.",
            "That mismatch makes Alice my vote.",
            "I keep Alice as my vote.",
            "Alice's unverified seer claim is my vote target.",
            "My final vote remains Alice.",
            "Conclusion: my vote: Alice.",
            "I will keep my vote on Alice.",
            "My vote stays on Alice.",
            "I maintain my vote for Alice.",
            "I keep voting Alice.",
        ):
            with self.subTest(text=text):
                self.assertEqual(named_target(text, self.names), "Alice")

    def test_final_affirmative_choice_wins(self) -> None:
        for text in (
            "I vote Bob. I now vote Alice.",
            "Declare your vote for Bob. I vote Alice.",
            "I will vote Bob; I change my vote to Alice.",
            "Bob is my vote. Alice is my final vote.",
        ):
            with self.subTest(text=text):
                self.assertEqual(named_target(text, self.names), "Alice")

    def test_explicit_current_vote_preferences_include_qualified_choices(self) -> None:
        for text in (
            "I currently lean toward voting Alice.",
            "I distrust Bob's framing and lean toward voting Alice.",
            "I lean toward Alice as today's vote candidate.",
            "I'm leaning toward Alice as today's execution target.",
            "I favor voting Alice over following Bob's push.",
            "I agree with voting Alice.",
            "I may vote Alice.",
            "I'd vote Alice for now.",
            "I provisionally vote Alice.",
            "Alice is my provisional vote.",
            "I favor Alice as today's vote.",
            "Bob's silence makes him my current vote candidate. I now vote Alice.",
            "I suspect Alice most and will vote there.",
            "@Bob I'll join your vote on Alice.",
            "I will vote Alice unless stronger evidence appears.",
        ):
            with self.subTest(text=text):
                self.assertEqual(named_target(text, self.names), "Alice")

    def test_direct_requests_and_coordination(self) -> None:
        for text in (
            "Wolf. don't vote me. Declare Alice and match my vote; Bob looks like the wolf.",
            "Bob. declare Alice as your vote.",
            "Wolf. declare a vote for villager candidate Alice.",
            "Bob. match my vote and execute Alice.",
            "Bob and I should vote Alice.",
            "Wolf. don't vote me-declare your vote for Alice.",
            "Alice is the villager-vote Alice with me. Bob.",
            "Bob. match my vote on Alice.",
            "I reject her claim. Vote Alice.",
            "Wolf. Do not vote me; declare Alice.",
        ):
            with self.subTest(text=text):
                self.assertEqual(named_target(text, self.names), "Alice")

    def test_pronouns_have_explicit_local_antecedents(self) -> None:
        for text in (
            "I reject Bob's claim. @Alice I vote for you.",
            "Alice, I vote you.",
            "@Alice Your unsupported claim makes you my vote.",
            "Alice is the seer. so I will vote for her; Bob. join me.",
            "Alice only cleared dead Bob. leaving no confirmation; her claim is my vote target.",
            "Alice is the villager candidate. Bob. match my vote on her.",
            "@Alice You are my vote.",
            "@Alice Your vote for Bob is premature. so I lean voting you.",
            "Bob is white. Alice's repeated echoing and Clara push make her my vote.",
        ):
            with self.subTest(text=text):
                self.assertEqual(named_target(text, self.names), "Alice")

    def test_ambiguous_pronouns_do_not_use_last_mentioned_name(self) -> None:
        for text in (
            "I vote him. Bob. join me.",
            "Alice is the seer and Bob is the villager; I vote him.",
            "Alice is suspicious. I vote you.",
            "@Alice @Bob I vote you.",
            "Alice is white. Bob repeatedly echoed Clara; I vote him.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(named_target(text, self.names))
        details = vote_target_details("I vote Alice. I now vote him.", self.names)
        self.assertIsNone(details["target"])
        self.assertTrue(details["unresolved"])

    def test_negations_questions_quotes_and_hypotheticals_are_not_votes(self) -> None:
        for text in (
            "I will not vote Alice.",
            "Don't vote Alice.",
            "Alice is not my vote.",
            "If I vote Alice, Bob wins.",
            "Your claim that I vote Alice is false.",
            'Bob said "I vote Alice". I have not chosen.',
            "Bob said 'I will vote Alice'.",
            "I vote Alice or Bob.",
            "Alice or Bob is my vote.",
            "Alice and Bob are my vote candidates.",
            "I may vote Alice. I may vote Bob.",
            "I vote Alice. I may vote Bob.",
            "Should I vote Alice?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(named_target(text, self.names))

    def test_alternatives_and_other_speakers_alone_are_not_declarations(self) -> None:
        for text in (
            "Bob's immediate vote on Alice looks forced.",
            "Bob rushed to vote Alice.",
            "I suspect Alice. Bob is my alternative.",
            "Alice's vote does not explain Bob.",
            "@Alice Explain your vote for Bob.",
            "Declare Alice.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(named_target(text, self.names))

    def test_name_boundaries_and_case_normalization(self) -> None:
        self.assertEqual(named_target("I vote aLiCe.", self.names), "Alice")
        self.assertIsNone(named_target("Malice is my vote.", self.names))
        self.assertIsNone(named_target("I vote Alice.", []))
        self.assertEqual(named_target("I vote Player-12.", ["Player-1", "Player-12"]), "Player-12")


class TestAffirmativeRoleClaims(unittest.TestCase):
    def test_affirmative_claims(self) -> None:
        for text, expected in (
            ("I am the possessed. Vote Alice.", "POSSESSED"),
            ("I'm the werewolf; vote Bob.", "WEREWOLF"),
            ("I\u2019m a wolf.", "WEREWOLF"),
            ("Conclusion: I am possessed.", "POSSESSED"),
            ("You doubt it, but I am the wolf.", "WEREWOLF"),
        ):
            with self.subTest(text=text):
                self.assertEqual(role_co(text), expected)

    def test_reported_denied_quoted_and_conditional_claims(self) -> None:
        for text in (
            "Her claim that I am wolf is false.",
            "I'm not convinced I'm the wolf.",
            "I am not the wolf.",
            "I deny I am the possessed.",
            "I reject the claim that I am wolf.",
            "If I am the wolf, vote me.",
            "Suppose I am the possessed.",
            'Bob said "I am the wolf".',
            "Bob said 'I'm possessed'.",
            "I am the wolf, according to Bob.",
            "I am the wolf?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(role_co(text))

    def test_report_does_not_suppress_independent_following_claim(self) -> None:
        self.assertEqual(role_co("Bob says I am the wolf. I am the possessed."), "POSSESSED")
        self.assertEqual(role_co('Bob said "I am possessed". I am the wolf.'), "WEREWOLF")


if __name__ == "__main__":
    unittest.main()
