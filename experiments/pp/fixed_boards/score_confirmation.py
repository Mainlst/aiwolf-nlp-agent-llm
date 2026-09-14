#!/usr/bin/env python3
"""Validate and deterministically score the excluded confirmation run."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
GENERATED = Path(__file__).resolve().parent / "generated"
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from experiments.pp.fixed_boards.scoring_rules import affirmative_role_co, declared_vote_target  # noqa: E402


def git(*args: str) -> str:
    """Run a read-only Git query for scoring provenance."""
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSON-object records."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def role_co(text: str) -> str | None:
    """Extract an affirmative self-claim using the versioned scoring rules."""
    return affirmative_role_co(text)


def named_target(text: str, names: list[str]) -> str | None:
    """Return a supported affirmative vote declaration, excluding others' votes."""
    return declared_vote_target(text, names)


def score_record(record: dict[str, Any], item: dict[str, Any], label: dict[str, Any]) -> dict[str, Any]:
    """Create deterministic component labels; leave semantic fields for the fixed judge."""
    talk = str(record.get("talk") or "")
    vote = str(record.get("vote") or "")
    names = list(item["input"]["snapshot"]["players"])
    co = role_co(talk)
    target = named_target(talk, names)
    established = bool(label["established_pp"])
    ally = label["correct_ally"]
    valid_targets = set(label["valid_village_targets"])
    return {
        "scenario_id": record["scenario_id"],
        "condition": record["condition"],
        "input_sha256": record["input_sha256"],
        "generation_success": bool(record.get("success")),
        "deterministic": {
            "role_co": co,
            "declared_vote_target": target,
            "submitted_vote_target": vote if vote in names else None,
            "talk_vote_match": target == vote if target is not None and vote in names else None,
            "ally_preserved": (target != ally and vote != ally) if established else None,
            "valid_village_target": (target in valid_targets and vote in valid_targets) if established else None,
            "unnecessary_role_co": co is not None if not established else None,
        },
        "semantic_fixed_judge_required": ["pp_recognized", "ally_recognized", "vote_coordination"],
        "excluded_from_confirmatory_analysis": True,
    }


def main() -> int:
    """Join the 12 scenarios, outputs, usage logs, and labels and verify the route."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--corpus-dir", type=Path, default=GENERATED)
    args = parser.parse_args()
    if git("status", "--porcelain"):
        raise RuntimeError("Refusing to score confirmation outputs from a dirty worktree")
    run_dir = args.run_dir.resolve()
    corpus_dir = args.corpus_dir.resolve()
    inputs = {item["scenario_id"]: item for item in read_jsonl(corpus_dir / "confirmation-inputs.jsonl")}
    labels = {item["scenario_id"]: item for item in read_jsonl(corpus_dir / "confirmation-labels.jsonl")}
    outputs = read_jsonl(run_dir / "outputs.jsonl")
    errors: list[str] = []
    expected_units = {(scenario_id, condition) for scenario_id in inputs for condition in ("P+", "P-PP")}
    actual_units = {(item["scenario_id"], item["condition"]) for item in outputs}
    if actual_units != expected_units or len(outputs) != len(expected_units):
        errors.append("confirmation output unit mismatch")
    scores: list[dict[str, Any]] = []
    game_ids: set[str] = set()
    for record in outputs:
        scenario_id = record["scenario_id"]
        if record.get("input_sha256") != inputs[scenario_id]["input_sha256"]:
            errors.append(f"{scenario_id}: input hash mismatch")
        if not record.get("success"):
            errors.append(f"{scenario_id} {record['condition']}: generation failure")
        game_ids.add(str(record.get("game_id")))
        scores.append(score_record(record, inputs[scenario_id], labels[scenario_id]))
    calls: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    for path in (run_dir / "agent-logs").rglob("llm_calls.jsonl"):
        calls.extend(read_jsonl(path))
    for path in (run_dir / "agent-logs").rglob("llm_usage.jsonl"):
        usage.extend(read_jsonl(path))
    if len(calls) != 48 or len(usage) != 48:
        errors.append(f"expected 48 call/usage records, found {len(calls)}/{len(usage)}")
    if any(call.get("success") is not True for call in calls):
        errors.append("failed LLM call in confirmation run")
    if any(call.get("response_model_id") != "gpt-5.6-luna" for call in calls):
        errors.append("unintended provider model in confirmation run")
    if {str(call.get("game_id")) for call in calls} != game_ids:
        errors.append("game_id join mismatch between output and call logs")
    requests = Counter(str(call.get("request")).lower() for call in calls)
    if requests != {"talk": 24, "vote": 24}:
        errors.append(f"request count mismatch: {dict(requests)}")
    result = {
        "schema_version": 1,
        "scoring_repository_commit": git("rev-parse", "HEAD"),
        "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "corpus_dir": str(corpus_dir.relative_to(ROOT) if corpus_dir.is_relative_to(ROOT) else corpus_dir),
        "corpus_files": {
            "confirmation-inputs.jsonl": hashlib.sha256((corpus_dir / "confirmation-inputs.jsonl").read_bytes()).hexdigest(),
            "confirmation-labels.jsonl": hashlib.sha256((corpus_dir / "confirmation-labels.jsonl").read_bytes()).hexdigest(),
        },
        "excluded_from_confirmatory_analysis": True,
        "scenario_count": len(inputs),
        "unit_count": len(outputs),
        "call_count": len(calls),
        "usage_count": len(usage),
        "request_counts": dict(sorted(requests.items())),
        "all_generation_succeeded": not any(not item.get("success") for item in outputs),
        "all_provider_models": sorted({call.get("response_model_id") for call in calls}),
        "game_id_join_complete": {str(call.get("game_id")) for call in calls} == game_ids,
        "semantic_policy": "deferred to the frozen judge; deterministic route only in this confirmation set",
        "scores": scores,
        "errors": errors,
    }
    (run_dir / "confirmation-score.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        "OK: 12 excluded scenarios; 24 TALK+VOTE units; 48 Luna calls; "
        "game_id/input-hash joins complete; deterministic scoring route valid",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
