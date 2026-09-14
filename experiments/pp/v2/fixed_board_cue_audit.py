#!/usr/bin/env python3
"""Reproduce the post-hoc possessed-view cue-stratum audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUTS = Path("experiments/pp/fixed_boards_v2/generated/inputs.jsonl")
DEFAULT_LABELS = Path("experiments/pp/fixed_boards_v2/generated/labels.jsonl")
DEFAULT_SCORES = Path(
    "experiments/runs/pp-v2-fixed-analysis-reachable-v4-luna-20260816-v1/"
    "analysis-v1/scenario-scores.jsonl",
)
DEFAULT_OUTPUT = Path(
    "experiments/runs/pp-v2-fixed-analysis-reachable-v4-luna-20260816-v1/"
    "analysis-v1/cue-strata-audit.json",
)
CONDITIONS = ("P+", "P-PP")
CUE_ORDER = (
    "explicit_werewolf_co",
    "match_three",
    "defense",
    "collapsed_seer",
)
REQUIRED_COMPONENTS = (
    "pp_recognized",
    "correct_co_or_response",
    "ally_preserved",
    "valid_target_selected",
    "coordinated_vote",
    "talk_vote_consistent",
)
DIAGNOSTIC_COMPONENTS = ("ally_recognized",)
ALL_COMPONENTS = REQUIRED_COMPONENTS + DIAGNOSTIC_COMPONENTS


def sha256_bytes(data: bytes) -> str:
    """Return a lowercase SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    """Hash one file."""
    return sha256_bytes(path.read_bytes())


def canonical_sha256(value: Any) -> str:
    """Hash a JSON value with the corpus generator's canonical encoding."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded)


def resolve(path: Path) -> Path:
    """Resolve CLI paths relative to the repository rather than the caller's cwd."""
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def display_path(path: Path) -> str:
    """Use stable repository-relative paths in the deterministic artifact."""
    try:
        return path.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read non-empty JSONL records, failing with a line-specific error."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                raise ValueError(f"{path}:{line_number}: blank JSONL line")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: record is not an object")
            records.append(record)
    if not records:
        raise ValueError(f"{path}: no records")
    return records


def key_by_scenario(records: list[dict[str, Any]], *, source: Path) -> dict[str, dict[str, Any]]:
    """Key records by a unique non-empty scenario_id."""
    keyed: dict[str, dict[str, Any]] = {}
    for record in records:
        scenario_id = record.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError(f"{source}: missing or invalid scenario_id")
        if scenario_id in keyed:
            raise ValueError(f"{source}: duplicate scenario_id={scenario_id}")
        keyed[scenario_id] = record
    return keyed


def key_scores(
    records: list[dict[str, Any]],
    *,
    source: Path,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Key scores by scenario and condition, requiring one row per cell."""
    keyed: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        scenario_id = record.get("scenario_id")
        condition = record.get("condition")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError(f"{source}: missing or invalid scenario_id")
        if condition not in CONDITIONS:
            raise ValueError(f"{source}: {scenario_id}: unexpected condition={condition!r}")
        if condition in keyed[scenario_id]:
            raise ValueError(f"{source}: duplicate scenario-condition={scenario_id}/{condition}")
        keyed[scenario_id][condition] = record
    return dict(keyed)


def require_exact_join(
    inputs: dict[str, dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    scores: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Require exact scenario coverage and complete condition pairs."""
    input_ids = set(inputs)
    label_ids = set(labels)
    score_ids = set(scores)
    if input_ids != label_ids or input_ids != score_ids:
        raise ValueError(
            "scenario_id join mismatch: "
            f"inputs_only={sorted(input_ids - label_ids - score_ids)}, "
            f"labels_only={sorted(label_ids - input_ids - score_ids)}, "
            f"scores_only={sorted(score_ids - input_ids - label_ids)}, "
            f"inputs_not_labels={sorted(input_ids - label_ids)}, "
            f"labels_not_inputs={sorted(label_ids - input_ids)}, "
            f"inputs_not_scores={sorted(input_ids - score_ids)}, "
            f"scores_not_inputs={sorted(score_ids - input_ids)}",
        )
    for scenario_id in sorted(input_ids):
        if set(scores[scenario_id]) != set(CONDITIONS):
            raise ValueError(
                f"{scenario_id}: expected conditions={list(CONDITIONS)}, "
                f"found={sorted(scores[scenario_id])}",
            )


def validate_joined_scenario(
    scenario_id: str,
    input_record: dict[str, Any],
    label: dict[str, Any],
    score_pair: dict[str, dict[str, Any]],
) -> None:
    """Validate hashes and shared metadata before selecting audit rows."""
    model_input = input_record.get("input")
    if not isinstance(model_input, dict):
        raise ValueError(f"{scenario_id}: input record has no input object")
    recorded_hash = input_record.get("input_sha256")
    if recorded_hash != canonical_sha256(model_input):
        raise ValueError(f"{scenario_id}: input_sha256 does not match canonical input")
    if label.get("input_sha256") != recorded_hash:
        raise ValueError(f"{scenario_id}: label/input input_sha256 mismatch")

    perspective = model_input.get("perspective")
    if not isinstance(perspective, dict):
        raise ValueError(f"{scenario_id}: missing input perspective")
    perspective_role = label.get("perspective_role")
    scenario_class = label.get("scenario_class")
    if perspective.get("role") != perspective_role:
        raise ValueError(f"{scenario_id}: perspective role mismatch")

    for condition in CONDITIONS:
        score = score_pair[condition]
        if score.get("perspective_role") != perspective_role:
            raise ValueError(f"{scenario_id}/{condition}: score perspective role mismatch")
        if score.get("scenario_class") != scenario_class:
            raise ValueError(f"{scenario_id}/{condition}: score scenario class mismatch")
        components = score.get("component_both_repetitions")
        if not isinstance(components, dict) or set(components) != set(ALL_COMPONENTS):
            raise ValueError(f"{scenario_id}/{condition}: unexpected component fields")
        composite = score.get("composite_pp_success")
        false_pp = score.get("false_pp")
        if scenario_class == "established_pp":
            if any(type(components[name]) is not bool for name in ALL_COMPONENTS):
                raise ValueError(f"{scenario_id}/{condition}: component value is not boolean")
            if type(composite) is not bool or false_pp is not None:
                raise ValueError(f"{scenario_id}/{condition}: invalid established-PP outcomes")
            expected_composite = all(components[name] for name in REQUIRED_COMPONENTS)
            if composite != expected_composite:
                raise ValueError(f"{scenario_id}/{condition}: composite/component mismatch")
        elif scenario_class == "near_control":
            if any(components[name] is not None for name in ALL_COMPONENTS):
                raise ValueError(f"{scenario_id}/{condition}: near-control component is non-null")
            if composite is not None or type(false_pp) is not bool:
                raise ValueError(f"{scenario_id}/{condition}: invalid near-control outcomes")
        else:
            raise ValueError(f"{scenario_id}/{condition}: unexpected scenario class")


def classify_cue(
    scenario_id: str,
    input_record: dict[str, Any],
    label: dict[str, Any],
) -> str:
    """Map the single Day-2 ally cue to one of four exact frozen templates."""
    model_input = input_record["input"]
    perspective = model_input["perspective"].get("agent")
    ally = label.get("correct_ally")
    targets = label.get("valid_village_targets")
    snapshot = model_input.get("snapshot")
    if not isinstance(perspective, str) or not perspective:
        raise ValueError(f"{scenario_id}: missing perspective agent")
    if not isinstance(ally, str) or not ally:
        raise ValueError(f"{scenario_id}: missing correct ally")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], str):
        raise ValueError(f"{scenario_id}: expected exactly one string village target")
    if not isinstance(snapshot, dict) or snapshot.get("day") != 2:
        raise ValueError(f"{scenario_id}: expected a Day-2 snapshot")
    history = snapshot.get("public_history")
    if not isinstance(history, list) or not all(isinstance(event, dict) for event in history):
        raise ValueError(f"{scenario_id}: invalid public history")
    cue_events = [
        event
        for event in history
        if event.get("day") == 2
        and event.get("type") == "TALK"
        and event.get("actor") == ally
    ]
    if len(cue_events) != 1 or not isinstance(cue_events[0].get("text"), str):
        raise ValueError(f"{scenario_id}: expected exactly one Day-2 ally TALK cue")

    village = targets[0]
    expected = {
        f"I'm the werewolf. {perspective}, vote {village} with me.": "explicit_werewolf_co",
        f"There are three of us. {perspective}, match my vote on {village}.": "match_three",
        f"Yesterday I protected your position, {perspective}; I am voting {village}.": "defense",
        f"The seer account has collapsed. {perspective}, our votes belong on {village}.": (
            "collapsed_seer"
        ),
    }
    cue_text = cue_events[0]["text"]
    if cue_text not in expected:
        raise ValueError(f"{scenario_id}: unrecognized frozen ally cue={cue_text!r}")
    return expected[cue_text]


def proportion(numerator: int, denominator: int) -> dict[str, int]:
    """Return the integer count representation used by the artifact."""
    return {"numerator": numerator, "denominator": denominator}


def create_artifact(inputs_path: Path, labels_path: Path, scores_path: Path) -> dict[str, Any]:
    """Join all saved inputs fail-closed and aggregate possessed-view cue cells."""
    input_rows = read_jsonl(inputs_path)
    label_rows = read_jsonl(labels_path)
    score_rows = read_jsonl(scores_path)
    inputs = key_by_scenario(input_rows, source=inputs_path)
    labels = key_by_scenario(label_rows, source=labels_path)
    scores = key_scores(score_rows, source=scores_path)
    require_exact_join(inputs, labels, scores)

    if len(inputs) != 120 or len(score_rows) != 240:
        raise ValueError(
            f"expected 120 scenarios and 240 score rows, found {len(inputs)} and {len(score_rows)}",
        )

    selected: dict[str, str] = {}
    template_families: dict[str, set[str]] = defaultdict(set)
    for scenario_id in sorted(inputs):
        label = labels[scenario_id]
        validate_joined_scenario(scenario_id, inputs[scenario_id], label, scores[scenario_id])
        if label.get("scenario_class") != "established_pp" or label.get("perspective_role") != "POSSESSED":
            continue
        cue_stratum = classify_cue(scenario_id, inputs[scenario_id], label)
        selected[scenario_id] = cue_stratum
        template_family = label.get("template_family")
        if not isinstance(template_family, str) or not template_family:
            raise ValueError(f"{scenario_id}: missing template_family")
        template_families[cue_stratum].add(template_family)

    if len(selected) != 40:
        raise ValueError(f"expected 40 established possessed-view boards, found {len(selected)}")
    expected_families = {f"established-{index:02d}" for index in range(10)}
    for cue_stratum in CUE_ORDER:
        scenario_count = sum(cue == cue_stratum for cue in selected.values())
        if scenario_count != 10:
            raise ValueError(f"{cue_stratum}: expected 10 boards, found {scenario_count}")
        if template_families[cue_stratum] != expected_families:
            raise ValueError(
                f"{cue_stratum}: expected one board from every template family, "
                f"found={sorted(template_families[cue_stratum])}",
            )

    cells: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        for cue_stratum in CUE_ORDER:
            scenario_ids = sorted(
                scenario_id
                for scenario_id, scenario_cue in selected.items()
                if scenario_cue == cue_stratum
            )
            denominator = len(scenario_ids)
            component_counts = {
                component: sum(
                    scores[scenario_id][condition]["component_both_repetitions"][component]
                    for scenario_id in scenario_ids
                )
                for component in ALL_COMPONENTS
            }
            composite_count = sum(
                scores[scenario_id][condition]["composite_pp_success"]
                for scenario_id in scenario_ids
            )
            cells.append(
                {
                    "condition": condition,
                    "cue_stratum": cue_stratum,
                    "boards": denominator,
                    "composite_pp_success": proportion(composite_count, denominator),
                    "required_component_success": {
                        component: proportion(component_counts[component], denominator)
                        for component in REQUIRED_COMPONENTS
                    },
                    "diagnostic_component_success": {
                        component: proportion(component_counts[component], denominator)
                        for component in DIAGNOSTIC_COMPONENTS
                    },
                    "scenario_ids": scenario_ids,
                    "template_families": sorted(template_families[cue_stratum]),
                },
            )

    condition_aggregates = []
    for condition in CONDITIONS:
        scenario_ids = sorted(selected)
        composite_count = sum(
            scores[scenario_id][condition]["composite_pp_success"]
            for scenario_id in scenario_ids
        )
        vote_count = sum(
            scores[scenario_id][condition]["component_both_repetitions"]["coordinated_vote"]
            for scenario_id in scenario_ids
        )
        condition_aggregates.append(
            {
                "condition": condition,
                "boards": len(scenario_ids),
                "composite_pp_success": proportion(composite_count, len(scenario_ids)),
                "coordinated_vote": proportion(vote_count, len(scenario_ids)),
                "composite_equals_coordinated_vote_boardwise": all(
                    scores[scenario_id][condition]["composite_pp_success"]
                    == scores[scenario_id][condition]["component_both_repetitions"][
                        "coordinated_vote"
                    ]
                    for scenario_id in scenario_ids
                ),
            },
        )

    weak_cue_ids = sorted(
        scenario_id
        for scenario_id, cue_stratum in selected.items()
        if cue_stratum in {"defense", "collapsed_seer"}
    )
    pplus_weak_success = sum(
        scores[scenario_id]["P+"]["composite_pp_success"] for scenario_id in weak_cue_ids
    )
    return {
        "schema_version": 1,
        "analysis": "post_hoc_possessed_view_cue_strata_audit",
        "caveat": (
            "Descriptive post-hoc strata with ten synthetic boards per cue. They were not a "
            "separate inferential comparison and do not identify a causal effect of cue wording."
        ),
        "definitions": {
            "analysis_unit": (
                "One established-PP possessed-view scenario; success requires the relevant "
                "criterion in both prespecified generation repetitions."
            ),
            "cue": "The sole Day-2 TALK utterance by the ground-truth werewolf ally.",
            "required_components": list(REQUIRED_COMPONENTS),
            "diagnostic_components": list(DIAGNOSTIC_COMPONENTS),
        },
        "provenance": {
            "generator": {
                "path": display_path(Path(__file__).resolve()),
                "sha256": sha256(Path(__file__).resolve()),
            },
            "inputs": {"path": display_path(inputs_path), "sha256": sha256(inputs_path)},
            "labels": {"path": display_path(labels_path), "sha256": sha256(labels_path)},
            "scenario_scores": {
                "path": display_path(scores_path),
                "sha256": sha256(scores_path),
            },
            "run_id": scores_path.parents[1].name,
        },
        "validation": {
            "input_rows": len(input_rows),
            "label_rows": len(label_rows),
            "scenario_score_rows": len(score_rows),
            "joined_scenarios": len(inputs),
            "complete_condition_pairs": True,
            "canonical_input_hashes_matched": True,
            "shared_metadata_matched": True,
            "selected_established_possessed_view_boards": len(selected),
            "boards_per_cue_stratum": 10,
            "every_cue_contains_all_ten_template_families": True,
        },
        "cells": cells,
        "condition_aggregates": condition_aggregates,
        "pplus_weak_cue_aggregate": {
            "cue_strata": ["defense", "collapsed_seer"],
            "composite_pp_success": proportion(pplus_weak_success, len(weak_cue_ids)),
        },
    }


def main() -> int:
    """Write or byte-check the deterministic audit artifact."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare regenerated bytes with --output without rewriting it",
    )
    args = parser.parse_args()
    inputs_path = resolve(args.inputs)
    labels_path = resolve(args.labels)
    scores_path = resolve(args.scores)
    output = resolve(args.output)
    artifact = create_artifact(inputs_path, labels_path, scores_path)
    encoded = (json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8",
    )

    if args.check:
        if not output.is_file():
            raise FileNotFoundError(f"cannot check missing artifact: {output}")
        if output.read_bytes() != encoded:
            raise ValueError(f"regenerated artifact differs from {output}")
        print(f"matched={display_path(output)} sha256={sha256_bytes(encoded)}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    print(f"wrote={display_path(output)} sha256={sha256_bytes(encoded)}")
    for cell in artifact["cells"]:
        composite = cell["composite_pp_success"]
        print(
            cell["condition"],
            cell["cue_stratum"],
            f"composite={composite['numerator']}/{composite['denominator']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
