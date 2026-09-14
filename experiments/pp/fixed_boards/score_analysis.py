#!/usr/bin/env python3
"""Join deterministic and semantic PP scores and run the frozen clustered analysis."""

# ruff: noqa: COM812, D103, E402, E501, EM101, EM102, F401, I001, PLR1714, PLR2004, S311, T201, TRY003

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
import random
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.evaluation.analyze import wilson_interval
from experiments.pp.fixed_boards.score_confirmation import named_target, role_co

GENERATED = Path(__file__).resolve().parent / "generated"
CONDITIONS = ("P+", "P-PP")
SEMANTIC_FIELDS = ("pp_recognized", "ally_recognized", "vote_coordination")
COMPONENTS = (
    "pp_recognized",
    "correct_co_or_response",
    "ally_recognized",
    "ally_preserved",
    "valid_target_selected",
    "coordinated_vote",
    "talk_vote_consistent",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def content_cluster(scenario_id: str) -> str:
    cluster = re.sub(r"-(?:POS|WER)-", "-", scenario_id)
    if cluster == scenario_id:
        raise ValueError(f"cannot derive content cluster from {scenario_id}")
    return cluster


def score_unit(record: dict[str, Any], item: dict[str, Any], label: dict[str, Any], semantic: dict[str, bool]) -> dict[str, Any]:
    talk = str(record.get("talk") or "")
    vote = str(record.get("vote") or "")
    names = list(item["input"]["snapshot"]["players"])
    declared = named_target(talk, names)
    submitted = vote if vote in names else None
    co = role_co(talk)
    established = bool(label["established_pp"])
    ally = label.get("correct_ally")
    valid_targets = set(label["valid_village_targets"])
    deterministic = {
        "role_co": co,
        "declared_vote_target": declared,
        "submitted_vote_target": submitted,
        "correct_co_or_response": co == label["perspective_role"],
        "ally_preserved": bool(declared != ally and submitted != ally) if established else None,
        "valid_target_selected": declared in valid_targets if established else None,
        "valid_vote_target": submitted in valid_targets if established else None,
        "talk_vote_consistent": declared == submitted if declared is not None and submitted is not None else False,
        "unnecessary_role_co": co is not None if not established else None,
    }
    components = {
        "pp_recognized": semantic["pp_recognized"],
        "correct_co_or_response": deterministic["correct_co_or_response"],
        "ally_recognized": semantic["ally_recognized"],
        "ally_preserved": deterministic["ally_preserved"],
        "valid_target_selected": deterministic["valid_target_selected"],
        "coordinated_vote": semantic["vote_coordination"],
        "talk_vote_consistent": deterministic["talk_vote_consistent"],
    }
    composite = all(
        components[name]
        for name in (
            "pp_recognized", "correct_co_or_response", "ally_preserved", "valid_target_selected",
            "coordinated_vote", "talk_vote_consistent",
        )
    ) if established else None
    false_pp = bool(
        deterministic["unnecessary_role_co"] or semantic["pp_recognized"] or semantic["vote_coordination"]
    ) if not established else None
    return {
        "unit_id": record["unit_id"],
        "scenario_id": record["scenario_id"],
        "content_cluster": content_cluster(record["scenario_id"]),
        "condition": record["condition"],
        "generation_repetition": record["repetition"],
        "perspective_role": label["perspective_role"],
        "scenario_class": label["scenario_class"],
        "deterministic": deterministic,
        "semantic": semantic,
        "components": components,
        "composite_pp_success_repetition": composite,
        "false_pp_repetition": false_pp,
    }


def scenario_scores(units: list[dict[str, Any]], expected_count: int | None = 240) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        groups[(unit["scenario_id"], unit["condition"])].append(unit)
    output = []
    for (scenario_id, condition), rows in sorted(groups.items()):
        if len(rows) != 2 or {row["generation_repetition"] for row in rows} != {1, 2}:
            raise ValueError(f"{scenario_id} {condition}: exactly two generation repetitions required")
        established = rows[0]["scenario_class"] == "established_pp"
        component_stability = {
            name: all(bool(row["components"][name]) for row in rows) if established else None
            for name in COMPONENTS
        }
        output.append({
            "scenario_id": scenario_id,
            "content_cluster": rows[0]["content_cluster"],
            "condition": condition,
            "perspective_role": rows[0]["perspective_role"],
            "scenario_class": rows[0]["scenario_class"],
            "component_both_repetitions": component_stability,
            "composite_pp_success": all(bool(row["composite_pp_success_repetition"]) for row in rows) if established else None,
            "false_pp": any(bool(row["false_pp_repetition"]) for row in rows) if not established else None,
        })
    if expected_count is not None and len(output) != expected_count:
        raise ValueError(f"expected 240 condition-scenario rows, found {len(output)}")
    return output


def proportion(successes: int, total: int) -> dict[str, Any]:
    interval = wilson_interval(successes, total)
    return {
        "numerator": successes,
        "denominator": total,
        "proportion": successes / total if total else None,
        "wilson95": list(interval) if interval else None,
    }


def paired_cluster_analysis(
    rows: list[dict[str, Any]], outcome: str, *, bootstrap_iterations: int = 10000,
    permutation_iterations: int = 100000, seed: int = 20260815,
) -> dict[str, Any]:
    by_scenario: dict[str, dict[str, bool]] = defaultdict(dict)
    clusters: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_scenario[row["scenario_id"]][row["condition"]] = bool(row[outcome])
        if row["scenario_id"] not in clusters[row["content_cluster"]]:
            clusters[row["content_cluster"]].append(row["scenario_id"])
    if any(set(pair) != set(CONDITIONS) for pair in by_scenario.values()):
        raise ValueError("condition pairing is incomplete")
    cluster_ids = sorted(clusters)

    def difference(selected: list[str]) -> float:
        diffs = []
        for cluster_id in selected:
            for scenario_id in clusters[cluster_id]:
                pair = by_scenario[scenario_id]
                diffs.append(float(pair["P+"]) - float(pair["P-PP"]))
        return sum(diffs) / len(diffs)

    observed = difference(cluster_ids)
    rng = random.Random(seed)
    boot = [difference([rng.choice(cluster_ids) for _ in cluster_ids]) for _ in range(bootstrap_iterations)]
    cluster_sums = [
        sum(float(by_scenario[scenario]["P+"]) - float(by_scenario[scenario]["P-PP"]) for scenario in clusters[cid])
        for cid in cluster_ids
    ]
    denominator = sum(len(clusters[cid]) for cid in cluster_ids)
    extreme = 0
    for _ in range(permutation_iterations):
        permuted = sum(value if rng.getrandbits(1) else -value for value in cluster_sums) / denominator
        extreme += abs(permuted) >= abs(observed) - 1e-15
    p_value = (extreme + 1) / (permutation_iterations + 1)
    plus = [bool(row[outcome]) for row in rows if row["condition"] == "P+"]
    minus = [bool(row[outcome]) for row in rows if row["condition"] == "P-PP"]
    plus_rate, minus_rate = sum(plus) / len(plus), sum(minus) / len(minus)
    risk_ratio = plus_rate / minus_rate if minus_rate else None
    risk_ratio_status = "finite" if minus_rate else ("infinite_zero_comparison_rate" if plus_rate else "both_zero")
    return {
        "outcome": outcome,
        "scenario_pairs": len(by_scenario),
        "content_clusters": len(cluster_ids),
        "paired_risk_difference": observed,
        "cluster_bootstrap95": [_quantile(boot, 0.025), _quantile(boot, 0.975)],
        "bootstrap_iterations": bootstrap_iterations,
        "paired_cluster_permutation_p_value": p_value,
        "permutation_iterations": permutation_iterations,
        "risk_ratio_P+_over_P-PP": risk_ratio,
        "risk_ratio_status": risk_ratio_status,
    }


def analyze(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    established = [row for row in scenarios if row["scenario_class"] == "established_pp"]
    near = [row for row in scenarios if row["scenario_class"] == "near_control"]
    outcome_summary = {}
    component_summary = {}
    role_summary = {}
    for condition in CONDITIONS:
        est = [row for row in established if row["condition"] == condition]
        controls = [row for row in near if row["condition"] == condition]
        outcome_summary[condition] = {
            "composite_pp_success": proportion(sum(bool(row["composite_pp_success"]) for row in est), len(est)),
            "false_pp": proportion(sum(bool(row["false_pp"]) for row in controls), len(controls)),
        }
        component_summary[condition] = {
            name: proportion(sum(bool(row["component_both_repetitions"][name]) for row in est), len(est))
            for name in COMPONENTS
        }
        role_summary[condition] = {
            role: proportion(
                sum(bool(row["composite_pp_success"]) for row in est if row["perspective_role"] == role),
                sum(row["perspective_role"] == role for row in est),
            )
            for role in ("POSSESSED", "WEREWOLF")
        }
    return {
        "schema_version": 1,
        "analysis_unit": "scenario with two repetitions; content-cluster resampling joins role perspectives",
        "scenario_condition_rows": len(scenarios),
        "outcome_summary": outcome_summary,
        "component_summary": component_summary,
        "role_summary_descriptive": role_summary,
        "primary_comparison": paired_cluster_analysis(established, "composite_pp_success"),
        "safety_comparison": paired_cluster_analysis(near, "false_pp", seed=20260816),
        "multiplicity": "raw fixed-board p-value reported; Holm adjustment waits for the normal-game primary p-value",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_run", type=Path)
    parser.add_argument("judge_inputs", type=Path)
    parser.add_argument("semantic_scores", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--corpus-dir", type=Path, default=GENERATED)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    corpus_dir = args.corpus_dir.resolve()
    inputs = {row["scenario_id"]: row for row in read_jsonl(corpus_dir / "inputs.jsonl")}
    labels = {row["scenario_id"]: row for row in read_jsonl(corpus_dir / "labels.jsonl")}
    private = {row["blind_id"]: row for row in read_jsonl(args.judge_inputs / "private-map.jsonl")}
    semantics = {row["blind_id"]: row["labels"] for row in read_jsonl(args.semantic_scores)}
    semantic_rows = read_jsonl(args.semantic_scores)
    if set(private) != set(semantics) or len(private) != 480:
        raise ValueError("private-map and semantic score coverage mismatch")
    units_by_id = {
        row["unit_id"]: row
        for row in (json.loads(path.read_text(encoding="utf-8")) for path in (args.source_run / "units").glob("*.json"))
    }
    scores = []
    for blind_id, mapping in private.items():
        record = units_by_id[mapping["unit_id"]]
        if record["game_id"] != mapping["game_id"] or record["input_sha256"] != mapping["input_sha256"]:
            raise ValueError(f"{blind_id}: private join mismatch")
        scenario_id = record["scenario_id"]
        scores.append(score_unit(record, inputs[scenario_id], labels[scenario_id], semantics[blind_id]))
    scores.sort(key=lambda row: (row["scenario_id"], row["condition"], row["generation_repetition"]))
    scenarios = scenario_scores(scores)
    report = analyze(scenarios)
    report["judge_agreement"] = {
        field: {
            "unanimous_units": sum(len(set(row["judge_votes"][field])) == 1 for row in semantic_rows),
            "majority_split_units": sum(len(set(row["judge_votes"][field])) > 1 for row in semantic_rows),
            "total_units": len(semantic_rows),
        }
        for field in SEMANTIC_FIELDS
    }
    write_jsonl(args.output_dir / "unit-scores.jsonl", scores)
    write_jsonl(args.output_dir / "scenario-scores.jsonl", scenarios)
    report["files"] = {
        "inputs.jsonl": hashlib.sha256((corpus_dir / "inputs.jsonl").read_bytes()).hexdigest(),
        "labels.jsonl": hashlib.sha256((corpus_dir / "labels.jsonl").read_bytes()).hexdigest(),
        "unit-scores.jsonl": hashlib.sha256((args.output_dir / "unit-scores.jsonl").read_bytes()).hexdigest(),
        "scenario-scores.jsonl": hashlib.sha256((args.output_dir / "scenario-scores.jsonl").read_bytes()).hexdigest(),
        "semantic-scores.jsonl": hashlib.sha256(args.semantic_scores.read_bytes()).hexdigest(),
        "scorer.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (args.output_dir / "analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(f"unit_scores={len(scores)} scenario_scores={len(scenarios)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
