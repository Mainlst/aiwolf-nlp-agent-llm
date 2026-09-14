#!/usr/bin/env python3
"""Generate an independent v2 confirmatory corpus without calling an API."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.pp.fixed_boards.generate import established, near_control, sha256_value, write_jsonl

DEFAULT_SPEC = Path(__file__).resolve().parent / "spec.yml"


def _counts(spec: dict[str, Any], analysis: bool, scenario_class: str, role: str) -> int:
    key = "analysis_counts" if analysis else "confirmation_counts"
    return int(spec[key][scenario_class][role])


def _source_index(spec: dict[str, Any], analysis: bool, scenario_class: str, index: int) -> int:
    group = "analysis" if analysis else "confirmation"
    short_class = "established" if scenario_class == "established_pp" else "near"
    generation = spec["generation"]
    return (
        int(generation[f"{group}_{short_class}_index_multiplier"]) * index
        + int(generation[f"{group}_{short_class}_index_shift"])
    )


def _ensure_werewolf_response_cue(item: dict[str, Any], label: dict[str, Any]) -> None:
    """Place every established wolf probe immediately after a visible possessed CO."""
    if label["scenario_class"] != "established_pp" or label["perspective_role"] != "WEREWOLF":
        return
    ally = str(label["correct_ally"])
    snapshot = item["input"]["snapshot"]
    already_visible = any(
        event.get("type") == "TALK"
        and event.get("actor") == ally
        and "possessed" in str(event.get("text", "")).casefold()
        for event in snapshot["public_history"]
    )
    if already_visible:
        return
    sequence = int(snapshot["cutoff_sequence"]) + 1
    snapshot["public_history"].append({
        "event_id": f"d{snapshot['day']}-e{sequence}",
        "day": int(snapshot["day"]),
        "sequence": sequence,
        "type": "TALK",
        "actor": ally,
        "text": "I am the possessed. Werewolf, reveal and name the villager we should vote for.",
    })
    snapshot["cutoff_sequence"] = sequence


def build_set_v2(spec: dict[str, Any], analysis: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a deterministic corpus whose scenario/input hashes differ from v1."""
    generation = spec["generation"]
    prefix = generation["analysis_prefix"] if analysis else generation["confirmation_prefix"]
    offset = int(generation["analysis_name_offset"] if analysis else generation["confirmation_name_offset"])
    inputs: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for role in ("POSSESSED", "WEREWOLF"):
        for scenario_class, factory in (("established_pp", established), ("near_control", near_control)):
            count = _counts(spec, analysis, scenario_class, role)
            marker = "E" if scenario_class == "established_pp" else "N"
            for index in range(count):
                source_index = _source_index(spec, analysis, scenario_class, index)
                item, label = factory(source_index, role, prefix, offset)
                scenario_id = f"{prefix}-{marker}-{role[:3]}-{index + 1:03d}"
                item["scenario_id"] = scenario_id
                item["input"]["source"] = spec["source"]["kind"]
                label["scenario_id"] = scenario_id
                label["exclude_from_confirmatory_analysis"] = not analysis
                _ensure_werewolf_response_cue(item, label)
                input_hash = sha256_value(item["input"])
                item["input_sha256"] = input_hash
                label["input_sha256"] = input_hash
                inputs.append(item)
                labels.append(label)
    return inputs, labels


def main() -> int:
    """Write the v2 corpus and a dependency-bound manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    args = parser.parse_args()
    spec_path = args.spec.resolve()
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    output = ROOT / spec["output_dir"]
    output.mkdir(parents=True, exist_ok=True)
    analysis_inputs, analysis_labels = build_set_v2(spec, analysis=True)
    confirmation_inputs, confirmation_labels = build_set_v2(spec, analysis=False)
    records = {
        "inputs.jsonl": analysis_inputs,
        "labels.jsonl": analysis_labels,
        "confirmation-inputs.jsonl": confirmation_inputs,
        "confirmation-labels.jsonl": confirmation_labels,
    }
    for name, values in records.items():
        write_jsonl(output / name, values)
    parent_generator = ROOT / spec["source"]["parent_grammar"]
    condition_manifest = ROOT / spec["conditions_dir"] / "manifest.json"
    evaluation_spec = ROOT / spec["evaluation_spec"]
    manifest = {
        "schema_version": 2,
        "study_id": spec["study_id"],
        "source": spec["source"],
        "analysis_scenarios": len(analysis_inputs),
        "confirmation_scenarios": len(confirmation_inputs),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "parent_grammar_sha256": hashlib.sha256(parent_generator.read_bytes()).hexdigest(),
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "condition_manifest_sha256": hashlib.sha256(condition_manifest.read_bytes()).hexdigest(),
        "evaluation_spec_sha256": hashlib.sha256(evaluation_spec.read_bytes()).hexdigest(),
        "files": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in records},
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Generated {len(analysis_inputs)} v2 analysis and {len(confirmation_inputs)} route-check scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
