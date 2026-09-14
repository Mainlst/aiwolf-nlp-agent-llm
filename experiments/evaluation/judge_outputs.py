#!/usr/bin/env python3
"""Validate judge JSONL, metric score ranges, and cited evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

METRICS = {
    "vote_consistency",
    "claim_consistency",
    "question_responsiveness",
    "evidence_bearing_accusation",
    "repetition",
    "role_action_quality",
    "dialogue_a",
    "dialogue_b",
    "dialogue_c",
    "dialogue_d",
    "dialogue_e",
}
LABELS = {"pass", "fail", "not_applicable", "insufficient_information"}
REQUIRED = {"metric", "label", "score", "rank", "evidence", "reason"}


def validate_output(output: dict[str, Any], judge_input: dict[str, Any] | None = None) -> list[str]:
    """Return all structural and evidence errors for one output."""
    errors: list[str] = []
    if set(output) != REQUIRED:
        errors.append(f"keys must be exactly {sorted(REQUIRED)}")
    metric = output.get("metric")
    label = output.get("label")
    score = output.get("score")
    rank = output.get("rank")
    evidence = output.get("evidence")
    reason = output.get("reason")
    if metric not in METRICS:
        errors.append("invalid metric")
    if label not in LABELS:
        errors.append("invalid label")
    if label in {"not_applicable", "insufficient_information"}:
        if score is not None:
            errors.append("non-evaluable labels require null score")
    elif metric in {
        "vote_consistency",
        "claim_consistency",
        "question_responsiveness",
        "evidence_bearing_accusation",
        "repetition",
    }:
        expected = 1 if label == "pass" else 0
        if score != expected:
            errors.append(f"binary {label} requires score {expected}")
    elif metric == "role_action_quality" and (
        not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 2
    ):
        errors.append("role_action_quality score must be 0..2")
    elif (
        isinstance(metric, str)
        and metric.startswith("dialogue_")
        and (not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5)
    ):
        errors.append("dialogue score must be 1..5")
    ranked = bool(judge_input and judge_input.get("comparison_mode") == "ranked_four")
    if ranked:
        if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= 4:
            errors.append("ranked comparison requires rank 1..4")
    elif rank is not None:
        errors.append("rank must be null outside ranked comparison")
    if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
        errors.append("reason must contain 1..500 characters")
    if not isinstance(evidence, list) or len(evidence) > 3:
        errors.append("evidence must be an array of at most three items")
        evidence = []
    if label in {"pass", "fail"} and not evidence:
        errors.append("pass/fail requires evidence")
    events = {str(event.get("event_id")): event for event in (judge_input or {}).get("public_history", [])}
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"event_id", "quote"}:
            errors.append("each evidence item requires only event_id and quote")
            continue
        event_id = str(item["event_id"])
        quote = item["quote"]
        if not isinstance(quote, str) or not 1 <= len(quote) <= 180:
            errors.append("evidence quote must contain 1..180 characters")
            continue
        if judge_input is not None:
            if event_id not in events:
                errors.append(f"unknown evidence event_id: {event_id}")
            else:
                haystack = json.dumps(events[event_id], ensure_ascii=False)
                if quote not in haystack:
                    errors.append(f"evidence quote not found in {event_id}")
    return errors


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def calibration_errors(path: Path) -> list[str]:
    """Validate calibration coverage and expected score conventions."""
    records = _jsonl(path)
    errors: list[str] = []
    covered = {record.get("metric") for record in records}
    missing = sorted(METRICS - covered)
    if missing:
        errors.append(f"calibration metrics missing: {missing}")
    for record in records:
        expected = record.get("expected", {})
        synthetic = {
            "metric": record.get("metric"),
            "label": expected.get("label"),
            "score": expected.get("score"),
            "rank": None,
            "evidence": [
                {
                    "event_id": record["public_history"][0]["event_id"],
                    "quote": next(
                        (
                            str(value)
                            for value in record["public_history"][0].values()
                            if isinstance(value, str) and value
                        ),
                        "event",
                    ),
                }
            ],
            "reason": "Calibration expectation.",
        }
        case_errors = validate_output(synthetic, record)
        errors.extend(f"{record.get('case_id')}: {error}" for error in case_errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--set", type=Path, default=Path("experiments/judge/calibration.jsonl"))
    validate = subparsers.add_parser("validate")
    validate.add_argument("outputs", type=Path)
    validate.add_argument("--inputs", type=Path)
    args = parser.parse_args()
    if args.command == "calibrate":
        errors = calibration_errors(args.set)
    else:
        outputs = _jsonl(args.outputs)
        inputs = _jsonl(args.inputs) if args.inputs else []
        by_id = {record.get("blind_id"): record for record in inputs}
        errors = []
        for index, output in enumerate(outputs, 1):
            judge_input = by_id.get(output.get("blind_id")) if inputs else None
            payload = {key: value for key, value in output.items() if key != "blind_id"}
            errors.extend(f"line {index}: {error}" for error in validate_output(payload, judge_input))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
