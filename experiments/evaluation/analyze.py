#!/usr/bin/env python3
"""Preregistered game-level analysis for normalized AIWolf experiment records."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    """Return the Wilson score interval for a binomial proportion."""
    if total <= 0 or successes < 0 or successes > total:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def cluster_bootstrap_difference(
    records: list[dict[str, Any]],
    reference: str,
    comparison: str,
    *,
    iterations: int = 5000,
    seed: int = 20260812,
) -> dict[str, Any] | None:
    """Estimate F-minus-ablation win-rate difference by resampling blocks."""
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["condition"] in {reference, comparison}:
            by_block[str(record["block"])].append(record)
    blocks = [
        block for block, rows in by_block.items() if {row["condition"] for row in rows} == {reference, comparison}
    ]
    if not blocks:
        return None

    def difference(selected_blocks: list[str]) -> float:
        wins = Counter()
        totals = Counter()
        for block in selected_blocks:
            for row in by_block[block]:
                condition = row["condition"]
                totals[condition] += 1
                wins[condition] += int(row["won"])
        return wins[reference] / totals[reference] - wins[comparison] / totals[comparison]

    observed = difference(blocks)
    rng = random.Random(seed)
    samples = [difference([rng.choice(blocks) for _ in blocks]) for _ in range(iterations)]
    return {
        "reference": reference,
        "comparison": comparison,
        "clusters": len(blocks),
        "difference": observed,
        "ci95": [_quantile(samples, 0.025), _quantile(samples, 0.975)],
    }


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    """Apply Holm's step-down family-wise error correction."""
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, (count - index) * value)
        adjusted[name] = min(1.0, running)
    return adjusted


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                left - factor * right for left, right in zip(augmented[row], augmented[column], strict=True)
            ]
    return [augmented[index][-1] for index in range(size)]


def _inverse(matrix: list[list[float]]) -> list[list[float]]:
    size = len(matrix)
    return [_solve(matrix, [1.0 if row == column else 0.0 for row in range(size)]) for column in range(size)]


def logistic_regression(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fit a game-level logistic model with condition, role, and pool fixed effects."""
    if len(records) < 2 or len({bool(row["won"]) for row in records}) < 2:
        return None
    conditions = sorted({str(row["condition"]) for row in records})
    roles = sorted({str(row["role"]) for row in records})
    pools = sorted({str(row["opponent_pool"]) for row in records})
    baseline_condition = "F" if "F" in conditions else conditions[0]
    names = ["intercept"]
    names += [f"condition[{value}]" for value in conditions if value != baseline_condition]
    names += [f"role[{value}]" for value in roles[1:]]
    names += [f"pool[{value}]" for value in pools[1:]]
    design: list[list[float]] = []
    outcomes: list[float] = []
    for record in records:
        row = [1.0]
        row += [float(record["condition"] == value) for value in conditions if value != baseline_condition]
        row += [float(record["role"] == value) for value in roles[1:]]
        row += [float(record["opponent_pool"] == value) for value in pools[1:]]
        design.append(row)
        outcomes.append(float(record["won"]))
    coefficients = [0.0] * len(names)
    information: list[list[float]] = []
    for _ in range(100):
        probabilities = [
            1 / (1 + math.exp(-max(-30.0, min(30.0, sum(b * x for b, x in zip(coefficients, row, strict=True))))))
            for row in design
        ]
        weights = [max(1e-8, probability * (1 - probability)) for probability in probabilities]
        information = [[0.0 for _ in names] for _ in names]
        gradient = [0.0 for _ in names]
        for row, outcome, probability, weight in zip(design, outcomes, probabilities, weights, strict=True):
            for left in range(len(names)):
                gradient[left] += row[left] * (outcome - probability)
                for right in range(len(names)):
                    information[left][right] += row[left] * weight * row[right]
        for index in range(len(names)):
            information[index][index] += 1e-6
        step = _solve(information, gradient)
        coefficients = [value + change for value, change in zip(coefficients, step, strict=True)]
        if max(abs(change) for change in step) < 1e-8:
            break
    covariance_columns = _inverse(information)
    covariance = [list(row) for row in zip(*covariance_columns, strict=True)]
    estimates = {}
    raw_condition_p = {}
    for index, name in enumerate(names):
        standard_error = math.sqrt(max(0.0, covariance[index][index]))
        z_value = coefficients[index] / standard_error if standard_error else 0.0
        p_value = math.erfc(abs(z_value) / math.sqrt(2))
        estimates[name] = {
            "coefficient": coefficients[index],
            "odds_ratio": math.exp(max(-30.0, min(30.0, coefficients[index]))),
            "standard_error": standard_error,
            "p_value": p_value,
        }
        if name.startswith("condition["):
            raw_condition_p[name] = p_value
    adjusted = holm_adjust(raw_condition_p)
    for name, value in adjusted.items():
        estimates[name]["holm_p_value"] = value
    return {"baseline_condition": baseline_condition, "terms": estimates}


def _group_summary(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[tuple(str(record[key]) for key in keys)].append(record)
    output = []
    for group, rows in sorted(groups.items()):
        wins = sum(int(row["won"]) for row in rows)
        interval = wilson_interval(wins, len(rows))
        output.append(
            {
                **dict(zip(keys, group, strict=True)),
                "wins": wins,
                "games": len(rows),
                "win_rate": wins / len(rows),
                "wilson95": list(interval) if interval else None,
            }
        )
    return output


def validate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and return included normalized game records."""
    required = {"game_id", "condition", "role", "opponent_pool", "block", "won"}
    included = []
    seen = set()
    for index, record in enumerate(records, 1):
        missing = required - record.keys()
        if missing:
            raise ValueError(f"record {index} missing {sorted(missing)}")
        if record["game_id"] in seen:
            raise ValueError(f"duplicate game_id: {record['game_id']}")
        seen.add(record["game_id"])
        if not isinstance(record["won"], bool):
            raise ValueError(f"record {index} won must be boolean")
        if not record.get("excluded", False):
            included.append(record)
    return included


def analyze(records: list[dict[str, Any]], bootstrap_iterations: int = 5000) -> dict[str, Any]:
    """Run the preregistered game-level summaries and primary comparisons."""
    included = validate_records(records)
    conditions = {str(record["condition"]) for record in included}
    comparisons = []
    if "F" in conditions:
        for condition in ("C", "R", "G"):
            if condition in conditions:
                result = cluster_bootstrap_difference(included, "F", condition, iterations=bootstrap_iterations)
                if result:
                    comparisons.append(result)
    return {
        "schema_version": 1,
        "analysis_unit": "game",
        "input_sha256": hashlib.sha256(
            "".join(json.dumps(record, sort_keys=True) for record in records).encode()
        ).hexdigest(),
        "included_games": len(included),
        "excluded_games": len(records) - len(included),
        "condition_summary": _group_summary(included, ("condition",)),
        "condition_role_summary": _group_summary(included, ("condition", "role")),
        "condition_pool_summary": _group_summary(included, ("condition", "opponent_pool")),
        "primary_differences": comparisons,
        "logistic_regression": logistic_regression(included),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Normalized game JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-iterations", type=int, default=5000)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = analyze(records, args.bootstrap_iterations)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"included_games={result['included_games']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
