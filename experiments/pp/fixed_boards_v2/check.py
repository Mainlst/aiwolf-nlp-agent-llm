#!/usr/bin/env python3
"""Fail-closed audit for the independent v2 fixed-board corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.pp.fixed_boards.check import audit_pair, nested_keys

DEFAULT_SPEC = Path(__file__).resolve().parent / "spec.yml"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    args = parser.parse_args()
    spec_path = args.spec.resolve()
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    output = ROOT / spec["output_dir"]
    legacy = ROOT / spec["legacy_output_dir"]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    dependencies = {
        "generator_sha256": Path(__file__).with_name("generate.py"),
        "parent_grammar_sha256": ROOT / spec["source"]["parent_grammar"],
        "spec_sha256": spec_path,
        "condition_manifest_sha256": ROOT / spec["conditions_dir"] / "manifest.json",
        "evaluation_spec_sha256": ROOT / spec["evaluation_spec"],
    }
    for key, path in dependencies.items():
        if manifest.get(key) != hashlib.sha256(path.read_bytes()).hexdigest():
            errors.append(f"dependency hash mismatch: {key}")
    for name, expected in manifest.get("files", {}).items():
        if hashlib.sha256((output / name).read_bytes()).hexdigest() != expected:
            errors.append(f"file hash mismatch: {name}")
    inputs = read_jsonl(output / "inputs.jsonl")
    labels = read_jsonl(output / "labels.jsonl")
    confirmation = read_jsonl(output / "confirmation-inputs.jsonl")
    confirmation_labels = read_jsonl(output / "confirmation-labels.jsonl")
    forbidden = set(spec["future_information_forbidden_keys"])
    for item, label in zip([*inputs, *confirmation], [*labels, *confirmation_labels], strict=True):
        errors.extend(audit_pair(item, label, forbidden))
        leaked = forbidden & nested_keys(item["input"])
        if leaked:
            errors.append(f"{item['scenario_id']}: leaked keys {sorted(leaked)}")
    expected = {
        (kind, role): int(count)
        for kind, role_counts in spec["analysis_counts"].items()
        for role, count in role_counts.items()
    }
    actual = Counter((label["scenario_class"], label["perspective_role"]) for label in labels)
    if dict(actual) != expected:
        errors.append(f"analysis count mismatch: {dict(actual)}")
    near = Counter(
        (label["perspective_role"], label["stratum"])
        for label in labels if label["scenario_class"] == "near_control"
    )
    expected_near = {
        (role, stratum): int(count)
        for role, strata in spec["near_control_strata"].items()
        for stratum, count in strata.items()
    }
    if dict(near) != expected_near:
        errors.append(f"near-control mismatch: {dict(near)}")
    analysis_hashes = {item["input_sha256"] for item in inputs}
    confirmation_hashes = {item["input_sha256"] for item in confirmation}
    legacy_hashes = {item["input_sha256"] for item in read_jsonl(legacy / "inputs.jsonl")}
    legacy_hashes |= {item["input_sha256"] for item in read_jsonl(legacy / "confirmation-inputs.jsonl")}
    if len(analysis_hashes) != len(inputs) or analysis_hashes & confirmation_hashes:
        errors.append("v2 analysis inputs are duplicated or overlap route checks")
    if (analysis_hashes | confirmation_hashes) & legacy_hashes:
        errors.append("v2 input hashes overlap legacy v1")
    if spec["source"]["tournament_logs_used"] or spec["source"]["legacy_outputs_used"]:
        errors.append("source provenance permits prohibited data")
    if not all(label.get("replay_reachable") is True for label in [*labels, *confirmation_labels]):
        errors.append("corpus contains a replay-unreachable decision snapshot")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        f"OK: {len(inputs)} independent analysis scenarios, {len(confirmation)} excluded route checks, "
        "all snapshots server-reachable, zero legacy hash overlap"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
