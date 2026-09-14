"""Tests for the frozen P+/P-PP configuration generator."""

# ruff: noqa: PT009

import re
from pathlib import Path
from unittest import TestCase

import yaml

from experiments.pp.generate_conditions import DEFAULT_SPEC, ROOT, generate, load_mapping, load_spec, nested_text


class PPConditionGenerationTest(TestCase):
    """Ensure the PP operation is exact and fail-closed."""

    @classmethod
    def setUpClass(cls) -> None:
        """Generate both conditions in memory without writing or API calls."""
        cls.spec = load_mapping(DEFAULT_SPEC)
        cls.base = load_mapping(ROOT / cls.spec["base_config"])
        cls.outputs = generate(cls.spec, cls.base)

    def test_registered_fragments_are_present_only_in_p_plus(self) -> None:
        """Every registered static sentence is removed exactly in P-PP."""
        for fragment in self.spec["static_fragments"]:
            plus_lines = nested_text(self.outputs["P+"], fragment["path"]).splitlines()
            minus_lines = nested_text(self.outputs["P-PP"], fragment["path"]).splitlines()
            self.assertIn(fragment["text"], plus_lines, fragment["id"])
            self.assertNotIn(fragment["text"], minus_lines, fragment["id"])

    def test_p_minus_has_no_forbidden_pp_residue(self) -> None:
        """The instruction surface of P-PP contains no PP marker."""
        minus_text = yaml.safe_dump(
            {"strategy": self.outputs["P-PP"]["strategy"], "prompt": self.outputs["P-PP"]["prompt"]},
            allow_unicode=True,
        )
        for pattern in self.spec["forbidden_in_p_minus"]:
            self.assertIsNone(re.search(pattern, minus_text), pattern)

    def test_shared_mechanisms_are_unchanged(self) -> None:
        """Model, guard, normal dynamic context, and role strategies stay enabled."""
        for condition in ("P+", "P-PP"):
            config = self.outputs[condition]
            self.assertEqual(config["llm"], self.base["llm"])
            self.assertEqual(config["agent"]["guard"], self.base["agent"]["guard"])
            self.assertEqual(config["openai"]["temperature"], 1.0)
            self.assertIn("Respond only in English. Never use Japanese.", config["prompt"]["talk_compact"])
            self.assertTrue(config["experiment"]["dynamic_context_enabled"])
            self.assertTrue(config["experiment"]["role_strategy_enabled"])

    def test_operation_is_deterministic(self) -> None:
        """The same base and spec always produce identical mappings."""
        self.assertEqual(generate(self.spec, self.base), self.outputs)


class PPV2ConditionGenerationTest(TestCase):
    """Keep the conflict-resolved v2 intervention exact and symmetric."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec_path = Path("experiments/pp/v2/spec.yml")
        cls.spec, _ = load_spec(cls.spec_path)
        cls.base = load_mapping(ROOT / cls.spec["base_config"])
        cls.outputs = generate(cls.spec, cls.base)

    def test_absolute_concealment_conflicts_are_absent_from_both_conditions(self) -> None:
        for condition in ("P+", "P-PP"):
            text = yaml.safe_dump(
                {"strategy": self.outputs[condition]["strategy"], "prompt": self.outputs[condition]["prompt"]},
                allow_unicode=True,
            )
            for pattern in self.spec["forbidden_conflicts_in_both_conditions"]:
                self.assertIsNone(re.search(pattern, text), (condition, pattern))

    def test_corrected_pp_bundle_is_present_only_in_p_plus(self) -> None:
        plus = yaml.safe_dump(self.outputs["P+"], allow_unicode=True)
        minus = yaml.safe_dump(self.outputs["P-PP"], allow_unicode=True)
        for pattern in self.spec["required_in_p_plus"]:
            self.assertIsNotNone(re.search(pattern, plus), pattern)
            self.assertIsNone(re.search(pattern, minus), pattern)

    def test_v2_generation_is_deterministic(self) -> None:
        self.assertEqual(generate(self.spec, self.base), self.outputs)
