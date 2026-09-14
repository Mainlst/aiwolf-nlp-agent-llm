#!/usr/bin/env python3
"""Prepare, run, validate, and aggregate the blind PP semantic judge."""

# ruff: noqa: ANN401, C901, D101, D103, E501, EM101, EM102, I001, PLR0911, PLR0912, PLR0915, PLR2004, Q000, S311, T201, TRY003

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import yaml
from dotenv import dotenv_values
from openai import APIConnectionError, AuthenticationError, BadRequestError, NotFoundError, OpenAI, PermissionDeniedError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
JUDGE = HERE / "judge"
EXPECTED_FIELDS = ("pp_recognized", "ally_recognized", "vote_coordination")
HIDDEN_KEYS = {
    "condition", "scenario_id", "generation_repetition", "repetition", "game_id", "source_prompt",
    "winner", "future_events", "hypothesis", "input_sha256", "unit_id",
}
FATAL_ERRORS = (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=180)


class BinaryClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: bool
    evidence: list[Evidence] = Field(min_length=1, max_length=3)
    reason: str = Field(min_length=1, max_length=400)


class PPJudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pp_recognized: BinaryClassification
    ally_recognized: BinaryClassification
    vote_coordination: BinaryClassification


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text("".join(canonical(value) + "\n" for value in values), encoding="utf-8")


def _digest(value: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}|{value}".encode()).hexdigest()


def _replace_names(text: str, mapping: dict[str, str]) -> str:
    output = text
    for source in sorted(mapping, key=len, reverse=True):
        output = re.sub(rf"(?<![\w-]){re.escape(source)}(?![\w-])", mapping[source], output)
    return output


def assert_blind(value: dict[str, Any], original_names: set[str]) -> None:
    serialized = canonical(value)
    leaked_names = sorted(name for name in original_names if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", serialized))
    if leaked_names:
        raise ValueError(f"original player names leaked: {leaked_names}")

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            leaked = HIDDEN_KEYS.intersection(item)
            if leaked:
                raise ValueError(f"hidden fields leaked: {sorted(leaked)}")
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)


def blind_unit(record: dict[str, Any], item: dict[str, Any], label: dict[str, Any], seed: str) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = item["input"]["snapshot"]
    names = {str(name) for name in snapshot["players"]}
    ordered = sorted(names, key=lambda name: _digest(name, f"{seed}|{item['scenario_id']}"))
    mapping = {name: f"Player-{index}" for index, name in enumerate(ordered, 1)}
    unit_key = f"{record['unit_id']}|{record['game_id']}"
    blind_id = _digest(unit_key, seed)[:24]
    history = []
    for event in snapshot["public_history"]:
        visible = {"event_id": event["event_id"], "type": event["type"]}
        if "actor" in event:
            visible["speaker"] = mapping[str(event["actor"])]
        if "target" in event:
            visible["target"] = mapping[str(event["target"])]
        if "text" in event:
            visible["text"] = _replace_names(str(event["text"]), mapping)
        history.append(visible)
    correct_ally = label.get("correct_ally")
    blind = {
        "schema_version": 1,
        "blind_id": blind_id,
        "perspective_player": mapping[item["input"]["perspective"]["agent"]],
        "public_history": history,
        "target_talk": {
            "event_id": "target-talk",
            "speaker": mapping[item["input"]["perspective"]["agent"]],
            "text": _replace_names(str(record["talk"]), mapping),
        },
        "target_vote": {
            "event_id": "target-vote",
            "speaker": mapping[item["input"]["perspective"]["agent"]],
            "target": mapping[str(record["vote"])],
        },
        "reference": {
            "perspective_role": item["input"]["perspective"]["role"],
            "alive_players": sorted(mapping[name] for name, status in snapshot["status_map"].items() if status == "ALIVE"),
            "correct_ally": mapping[str(correct_ally)] if correct_ally else None,
            "valid_village_targets": sorted(mapping[str(name)] for name in label["valid_village_targets"]),
        },
    }
    assert_blind(blind, names)
    private = {
        "blind_id": blind_id,
        "unit_id": record["unit_id"],
        "scenario_id": record["scenario_id"],
        "condition": record["condition"],
        "generation_repetition": record["repetition"],
        "game_id": record["game_id"],
        "input_sha256": record["input_sha256"],
        "player_mapping": mapping,
    }
    return blind, private


def prepare(
    run_dir: Path,
    output_dir: Path,
    seed: str = "aiwolfdial-2026-pp-judge-v1",
    corpus_dir: Path = GENERATED,
) -> dict[str, Any]:
    inputs = {row["scenario_id"]: row for row in read_jsonl(corpus_dir / "inputs.jsonl")}
    labels = {row["scenario_id"]: row for row in read_jsonl(corpus_dir / "labels.jsonl")}
    operational = json.loads((run_dir / "operational-summary.json").read_text(encoding="utf-8"))
    expected_operational = {
        "planned_units": 480,
        "completed_units": 480,
        "observed_call_records": 960,
        "observed_usage_records": 960,
        "validated_units": 480,
        "critical_error_units": 0,
        "complete": True,
    }
    if any(operational.get(key) != value for key, value in expected_operational.items()):
        raise ValueError("source operational summary is incomplete or invalid")
    if operational.get("provider_models") != ["gpt-5.6-luna"]:
        raise ValueError("source generation used an unexpected provider model")
    units = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_dir / "units").glob("*.json"))]
    if len(units) != 480 or any(not row.get("validation", {}).get("passed") for row in units):
        raise ValueError("source run must contain 480 validated units")
    combined = [blind_unit(row, inputs[row["scenario_id"]], labels[row["scenario_id"]], seed) for row in units]
    blinds = [pair[0] for pair in combined]
    private = [pair[1] for pair in combined]
    if len({row["blind_id"] for row in blinds}) != 480:
        raise ValueError("blind IDs are not unique")
    output_dir.mkdir(parents=True, exist_ok=False)
    write_jsonl(output_dir / "blind-inputs.jsonl", blinds)
    write_jsonl(output_dir / "private-map.jsonl", private)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "source_run": run_dir.name,
        "corpus_dir": str(corpus_dir.relative_to(ROOT) if corpus_dir.is_relative_to(ROOT) else corpus_dir),
        "unit_count": len(blinds),
        "directional_results_inspected": False,
        "seed": seed,
        "files": {
            "source-manifest.json": sha256(run_dir / "manifest.json"),
            "source-operational-summary.json": sha256(run_dir / "operational-summary.json"),
            "inputs.jsonl": sha256(corpus_dir / "inputs.jsonl"),
            "labels.jsonl": sha256(corpus_dir / "labels.jsonl"),
            "config.yml": sha256(JUDGE / "config.yml"),
            "rubric.md": sha256(JUDGE / "rubric.md"),
            "calibration.jsonl": sha256(JUDGE / "calibration.jsonl"),
            "runner.py": sha256(Path(__file__)),
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def evidence_errors(result: dict[str, Any], judge_input: dict[str, Any]) -> list[str]:
    events = {str(event["event_id"]): event for event in judge_input["public_history"]}
    events["target-talk"] = judge_input["target_talk"]
    errors = []
    if set(result) != set(EXPECTED_FIELDS):
        errors.append("result keys differ from the three registered classifiers")
    for field in EXPECTED_FIELDS:
        value = result.get(field, {})
        for evidence in value.get("evidence", []):
            event_id = str(evidence.get("event_id"))
            quote = evidence.get("quote")
            if event_id not in events:
                errors.append(f"{field}: unknown event ID {event_id}")
            elif not isinstance(quote, str) or quote not in canonical(events[event_id]):
                errors.append(f"{field}: quote is absent from {event_id}")
    return errors


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    raw = usage.model_dump() if hasattr(usage, "model_dump") else {}
    details = raw.get("input_tokens_details") or {}
    return {
        "input_tokens": int(raw.get("input_tokens") or 0),
        "cached_input_tokens": int(details.get("cached_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def call_judge(client: OpenAI, judge_input: dict[str, Any], config: dict[str, Any], instructions: str) -> dict[str, Any]:
    started = monotonic()
    try:
        request: dict[str, Any] = {
            "model": config["model"],
            "reasoning": {"effort": config["reasoning_effort"]},
            "max_output_tokens": config["max_output_tokens"],
            "instructions": instructions,
            "input": canonical(judge_input),
            "text_format": PPJudgeOutput,
            "store": False,
            "timeout": config["timeout_seconds"],
        }
        if config.get("temperature") is not None:
            request["temperature"] = config["temperature"]
        response = client.responses.parse(
            **request,
        )
        parsed = response.output_parsed
        result = parsed.model_dump() if parsed is not None else None
        errors = ["structured output was absent"] if result is None else evidence_errors(result, judge_input)
        return {
            "success": not errors,
            "response_id": response.id,
            "response_model_id": response.model,
            "result": result,
            "validation_errors": errors,
            "usage": _usage(response),
            "elapsed_seconds": round(monotonic() - started, 6),
            "error": None,
        }
    except Exception as error:  # noqa: BLE001
        return {
            "success": False,
            "response_id": None,
            "response_model_id": None,
            "result": None,
            "validation_errors": [],
            "usage": {},
            "elapsed_seconds": round(monotonic() - started, 6),
            "error": {"type": type(error).__name__, "message": str(error)},
        }


def estimated_cost(usage: dict[str, int], config: dict[str, Any]) -> float:
    prices = config["pricing_usd_per_million_tokens"]
    cached = usage.get("cached_input_tokens", 0)
    uncached = usage.get("input_tokens", 0) - cached
    return (uncached * prices["input"] + cached * prices["cached_input"] + usage.get("output_tokens", 0) * prices["output"]) / 1_000_000


def run_batch(input_path: Path, run_dir: Path, max_parallel: int, repetitions: int | None = None) -> int:
    config = yaml.safe_load((JUDGE / "config.yml").read_text(encoding="utf-8"))
    repetitions = repetitions or int(config["repetitions"])
    if max_parallel < 1:
        raise ValueError("max_parallel must be positive")
    if run_dir.exists():
        raise FileExistsError(f"refusing to overwrite {run_dir}")
    run_dir.mkdir(parents=True)
    (run_dir / "results").mkdir()
    inputs = read_jsonl(input_path)
    by_id = {row["blind_id"]: row for row in inputs}
    schedule = [(row["blind_id"], repetition) for row in inputs for repetition in range(1, repetitions + 1)]
    random.Random(int(config["order_seed"])).shuffle(schedule)
    instructions = (JUDGE / "rubric.md").read_text(encoding="utf-8")
    key = str(dotenv_values(ROOT / "config" / ".env").get("OPENAI_API_KEY") or "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is absent")
    client = OpenAI(api_key=key, max_retries=0)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "model": config["model"],
        "temperature": config["temperature"],
        "reasoning_effort": config["reasoning_effort"],
        "unit_count": len(inputs),
        "repetitions": repetitions,
        "planned_calls": len(schedule),
        "max_parallel": max_parallel,
        "input_sha256": sha256(input_path),
        "config_sha256": sha256(JUDGE / "config.yml"),
        "rubric_sha256": sha256(JUDGE / "rubric.md"),
        "runner_sha256": sha256(Path(__file__)),
    }
    write_json(run_dir / "manifest.json", manifest)
    attempts: Counter[tuple[str, int]] = Counter()
    pending = list(schedule)
    running: dict[Future[dict[str, Any]], tuple[str, int]] = {}
    usage_total: Counter[str] = Counter()
    spent = 0.0
    fatal = False
    completed = 0
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(schedule))) as executor:
        while pending or running:
            while pending and not fatal and len(running) < max_parallel:
                blind_id, repetition = pending.pop(0)
                attempts[(blind_id, repetition)] += 1
                future = executor.submit(call_judge, client, by_id[blind_id], config, instructions)
                running[future] = (blind_id, repetition)
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                blind_id, repetition = running.pop(future)
                record = future.result()
                record.update({
                    "blind_id": blind_id,
                    "judge_repetition": repetition,
                    "attempt": attempts[(blind_id, repetition)],
                    "completed_at": datetime.now(UTC).astimezone().isoformat(),
                })
                for key_name, value in record["usage"].items():
                    usage_total[key_name] += value
                call_cost = estimated_cost(record["usage"], config)
                spent += call_cost
                record["estimated_cost_usd"] = round(call_cost, 9)
                error_type = (record.get("error") or {}).get("type")
                if record.get("response_model_id") not in {None, config["model"]}:
                    record["validation_errors"].append("provider model changed")
                    record["success"] = False
                    fatal = True
                if error_type in {error.__name__ for error in FATAL_ERRORS}:
                    fatal = True
                if call_cost > float(config["per_call_cost_limit_usd"]) or spent > float(config["batch_cost_limit_usd"]):
                    record["validation_errors"].append("cost stopping rule reached")
                    record["success"] = False
                    fatal = True
                retry = (
                    not fatal and not record["success"] and not record["error"]
                    and attempts[(blind_id, repetition)] <= int(config["max_invalid_retries"])
                )
                if retry:
                    pending.insert(0, (blind_id, repetition))
                    state = "retry-invalid"
                else:
                    write_json(run_dir / "results" / f"{blind_id}__R{repetition}.json", record)
                    completed += 1
                    state = "validated" if record["success"] else "retained-failure"
                summary = {
                    "planned_calls": len(schedule), "completed_calls": completed, "api_attempts": sum(attempts.values()),
                    "successful_calls": len([p for p in (run_dir / 'results').glob('*.json') if json.loads(p.read_text()).get('success')]),
                    "provider_models": sorted({json.loads(p.read_text()).get('response_model_id') for p in (run_dir / 'results').glob('*.json') if json.loads(p.read_text()).get('response_model_id')}),
                    "usage": dict(usage_total), "estimated_cost_usd": round(spent, 6), "fatal_stop": fatal,
                    "updated_at": datetime.now(UTC).astimezone().isoformat(),
                }
                write_json(run_dir / "operational-summary.json", summary)
                print(f"{blind_id} judge_r={repetition} attempt={attempts[(blind_id, repetition)]} state={state} completed={completed}/{len(schedule)}", flush=True)
    return 0 if completed == len(schedule) and not fatal and all(json.loads(p.read_text()).get("success") for p in (run_dir / "results").glob("*.json")) else 1


def aggregate(judge_run: Path, output_path: Path) -> dict[str, Any]:
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((judge_run / "results").glob("*.json"))]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault(row["blind_id"], []).append(row)
    output = []
    for blind_id, rows in sorted(grouped.items()):
        if len(rows) != 3 or any(not row["success"] for row in rows):
            raise ValueError(f"{blind_id}: three valid repetitions required")
        labels = {}
        votes = {}
        for field in EXPECTED_FIELDS:
            values = [bool(row["result"][field]["label"]) for row in rows]
            labels[field] = sum(values) >= 2
            votes[field] = values
        output.append({"blind_id": blind_id, "labels": labels, "judge_votes": votes})
    if len(output) != 480:
        raise ValueError(f"expected 480 aggregated units, found {len(output)}")
    write_jsonl(output_path, output)
    return {"unit_count": len(output), "output_sha256": sha256(output_path)}


def calibration_errors(path: Path) -> list[str]:
    errors = []
    for row in read_jsonl(path):
        judge_input = row["input"]
        assert_blind(judge_input, set())
        expected = row["expected"]
        if set(expected) != set(EXPECTED_FIELDS) or not all(isinstance(value, bool) for value in expected.values()):
            errors.append(f"{row.get('case_id')}: invalid expected labels")
    return errors


def check_live_calibration(run_dir: Path) -> list[str]:
    """Compare validated live calibration results with frozen expected labels."""
    expected = {row["input"]["blind_id"]: row["expected"] for row in read_jsonl(JUDGE / "calibration.jsonl")}
    results = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_dir / "results").glob("*.json"))]
    errors = []
    if len(results) != len(expected):
        errors.append(f"expected {len(expected)} live results, found {len(results)}")
    for row in results:
        blind_id = row["blind_id"]
        if not row.get("success"):
            errors.append(f"{blind_id}: live call failed validation")
            continue
        observed = {field: bool(row["result"][field]["label"]) for field in EXPECTED_FIELDS}
        if observed != expected.get(blind_id):
            errors.append(f"{blind_id}: expected {expected.get(blind_id)}, observed {observed}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("run_dir", type=Path)
    prep.add_argument("output_dir", type=Path)
    prep.add_argument("--corpus-dir", type=Path, default=GENERATED)
    prep.add_argument("--seed", default="aiwolfdial-2026-pp-judge-v1")
    cal = sub.add_parser("calibrate")
    cal.add_argument("--live-run", type=Path)
    cal.add_argument("--check-run", type=Path)
    cal.add_argument("--max-parallel", type=int, default=7)
    run = sub.add_parser("run")
    run.add_argument("input", type=Path)
    run.add_argument("run_dir", type=Path)
    run.add_argument("--max-parallel", type=int, default=24)
    agg = sub.add_parser("aggregate")
    agg.add_argument("judge_run", type=Path)
    agg.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(
            args.run_dir.resolve(),
            args.output_dir.resolve(),
            seed=args.seed,
            corpus_dir=args.corpus_dir.resolve(),
        )
        print(f"blind_units={result['unit_count']}")
        return 0
    if args.command == "calibrate":
        source = JUDGE / "calibration.jsonl"
        errors = calibration_errors(source)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        if args.live_run:
            temporary = args.live_run.parent / f"{args.live_run.name}-inputs.jsonl"
            write_jsonl(temporary, [row["input"] for row in read_jsonl(source)])
            return run_batch(temporary, args.live_run, args.max_parallel, repetitions=1)
        if args.check_run:
            errors = check_live_calibration(args.check_run)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print(f"OK: {len(read_jsonl(source))} live calibration cases matched")
            return 0
        print(f"OK: {len(read_jsonl(source))} calibration cases")
        return 0
    if args.command == "run":
        return run_batch(args.input.resolve(), args.run_dir.resolve(), args.max_parallel)
    result = aggregate(args.judge_run.resolve(), args.output.resolve())
    print(f"aggregated_units={result['unit_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
