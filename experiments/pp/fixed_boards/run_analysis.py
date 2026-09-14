#!/usr/bin/env python3
"""Run the frozen 120-board P+/P-PP analysis with fail-closed auditing."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import yaml
from ulid import ULID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from aiwolf_nlp_common.packet import Request, Role, Talk  # noqa: E402

from agent.possessed import Possessed  # noqa: E402
from agent.werewolf import Werewolf  # noqa: E402
from experiments.pp.fixed_boards.run_confirmation import (  # noqa: E402
    AGENT_LOCKS,
    build_info,
    close_agent_logging,
    read_jsonl,
    sha256,
)

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
CONDITIONS = ("P+", "P-PP")
EXPECTED_MODEL = "gpt-5.6-luna"
REPETITIONS = 2
ORDER_SEED = 20260815
FATAL_INFRASTRUCTURE_ERRORS = {
    "APIConnectionError",
    "AuthenticationError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
}


def git(*args: str) -> str:
    """Run a read-only Git query for provenance."""
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def git_bytes(*args: str) -> bytes:
    """Return byte-exact Git output for provenance snapshots."""
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def canonical_json(value: Any) -> bytes:
    """Return a stable JSON representation for hashes."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def unit_id(scenario_id: str, condition: str, repetition: int) -> str:
    """Build a filesystem-safe, stable experimental-unit ID."""
    condition_id = "PPLUS" if condition == "P+" else "PMINUSPP"
    return f"{scenario_id}__{condition_id}__R{repetition}"


def write_json(path: Path, value: Any) -> None:
    """Atomically replace one JSON artifact."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    """Append one main-thread-owned audit event."""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def load_jsonl_if_present(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL artifact, returning an empty list when absent."""
    return read_jsonl(path) if path.exists() else []


def live_identity_errors(log_dir: Path, game_id: str, condition: str) -> list[str]:
    """Fail before VOTE when the just-finished TALK call has invalid provenance."""
    calls = load_jsonl_if_present(log_dir / "llm_calls.jsonl")
    if len(calls) != 1:
        return [f"expected one completed TALK call before VOTE, found {len(calls)}"]
    call = calls[0]
    errors: list[str] = []
    if str(call.get("request", "")).lower() != "talk":
        errors.append("first request is not TALK")
    if str(call.get("game_id")) != game_id:
        errors.append("TALK game_id join mismatch")
    if str(call.get("condition")) != condition:
        errors.append("TALK condition join mismatch")
    response_model = call.get("response_model_id")
    if isinstance(response_model, str) and response_model and response_model != EXPECTED_MODEL:
        errors.append("TALK used an unintended provider model")
    error_type = call.get("error_type")
    if error_type in FATAL_INFRASTRUCTURE_ERRORS:
        errors.append(f"fatal TALK infrastructure error: {error_type}")
    return errors


def validate_attempt(record: dict[str, Any], log_dir: Path, expected_input_sha256: str) -> dict[str, Any]:
    """Validate one completed unit without inspecting condition-specific behavior."""
    calls = load_jsonl_if_present(log_dir / "llm_calls.jsonl")
    usage = load_jsonl_if_present(log_dir / "llm_usage.jsonl")
    errors: list[str] = []
    critical_errors: list[str] = []
    expected_requests = ["talk", "vote"]
    requests = [str(call.get("request", "")).lower() for call in calls]
    response_models = sorted(
        {model for call in calls if isinstance((model := call.get("response_model_id")), str) and model},
    )

    if record.get("input_sha256") != expected_input_sha256:
        critical_errors.append("input hash mismatch")
    if record.get("configured_model") != EXPECTED_MODEL:
        critical_errors.append("configured model mismatch")
    if any(model != EXPECTED_MODEL for model in response_models):
        critical_errors.append(f"unintended provider model: {response_models}")
    if any(str(call.get("game_id")) != record.get("game_id") for call in calls):
        critical_errors.append("game_id join mismatch")
    if any(str(call.get("condition")) != record.get("condition") for call in calls):
        critical_errors.append("condition join mismatch")
    if len(calls) > 2 or len(usage) > 2:
        critical_errors.append(f"more than two call/usage records: {len(calls)}/{len(usage)}")
    if requests != expected_requests[: len(requests)]:
        critical_errors.append(f"request order mismatch: {requests}")
    if record.get("generation_success"):
        if requests != expected_requests:
            critical_errors.append(f"successful unit lacks TALK/VOTE call pair: {requests}")
        if len(usage) != len([call for call in calls if call.get("success") is True]):
            critical_errors.append(f"call/usage success mismatch: {len(calls)}/{len(usage)}")
        if any(call.get("success") is not True for call in calls):
            errors.append("TALK/VOTE pair contains a recorded failed model call")
    elif not calls:
        errors.append("generation failed before an LLM call was recorded")
    else:
        errors.append("recorded LLM request did not yield an evaluable TALK/VOTE pair")

    if len(usage) > len(calls):
        critical_errors.append("usage count exceeds call count")
    usage_game_ids = {str(item.get("game_id")) for item in usage}
    if usage_game_ids and usage_game_ids != {str(record.get("game_id"))}:
        critical_errors.append("usage game_id join mismatch")
    if any(str(item.get("response_model_id")) != EXPECTED_MODEL for item in usage):
        critical_errors.append("unintended provider model in usage")
    fatal_call_errors = sorted(
        {str(call.get("error_type")) for call in calls if call.get("error_type") in FATAL_INFRASTRUCTURE_ERRORS},
    )
    if fatal_call_errors:
        critical_errors.append(f"fatal LLM infrastructure errors: {fatal_call_errors}")

    return {
        "checked_at": datetime.now(UTC).astimezone().isoformat(),
        "call_count": len(calls),
        "usage_count": len(usage),
        "requests": requests,
        "response_models": response_models,
        "errors": errors,
        "critical_errors": critical_errors,
        "passed": not errors and not critical_errors,
    }


def run_attempt(
    run_dir: Path,
    item: dict[str, Any],
    condition: str,
    repetition: int,
    attempt: int,
    conditions_dir: Path = ROOT / "experiments" / "pp" / "generated",
) -> dict[str, Any]:
    """Generate TALK then VOTE once and audit its isolated logs."""
    scenario_id = str(item["scenario_id"])
    stable_unit_id = unit_id(scenario_id, condition, repetition)
    role = str(item["input"]["perspective"]["role"])
    agent_name = str(item["input"]["perspective"]["agent"])
    game_id = str(ULID())
    log_dir = run_dir / "agent-logs" / stable_unit_id / f"attempt-{attempt}"
    config_path = conditions_dir / f"{condition}.yml"
    config = deepcopy(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    config["llm"]["sleep_time"] = 0
    config["log"]["console_output"] = False
    config["log"]["output_dir"] = str(log_dir)
    config["agent"]["default_action_timeout_seconds"] = 120
    agent_class = Possessed if role == "POSSESSED" else Werewolf
    started = monotonic()
    started_at = datetime.now(UTC).astimezone().isoformat()

    with AGENT_LOCKS[agent_name]:
        agent = agent_class(config, agent_name, game_id, Role(role))
        info, history = build_info(item, game_id)
        agent.info = info
        agent.talk_history = history
        live_errors: list[str] = []
        try:
            agent.initialize()
            agent.request = Request.TALK
            talk = agent.talk()
            live_errors = live_identity_errors(log_dir, game_id, condition)
            if live_errors:
                raise RuntimeError("TALK provenance validation failed before VOTE")
            agent.talk_history.append(
                Talk(
                    idx=max((entry.idx for entry in history), default=0) + 1,
                    day=info.day,
                    turn=max((entry.turn for entry in history if entry.day == info.day), default=0) + 1,
                    agent=agent_name,
                    text=talk,
                ),
            )
            agent.request = Request.VOTE
            vote = agent.vote()
            error = None
        except Exception as exception:  # noqa: BLE001
            talk = locals().get("talk")
            vote = None
            error = {"type": type(exception).__name__, "message": str(exception)}
        finally:
            close_agent_logging(agent)

    record = {
        "unit_id": stable_unit_id,
        "scenario_id": scenario_id,
        "input_sha256": item["input_sha256"],
        "condition": condition,
        "repetition": repetition,
        "attempt": attempt,
        "game_id": game_id,
        "configured_model": config["openai"]["model"],
        "configured_temperature": config["openai"]["temperature"],
        "talk": talk,
        "vote": vote,
        "generation_success": error is None and isinstance(talk, str) and isinstance(vote, str),
        "error": error,
        "started_at": started_at,
        "elapsed_seconds": round(monotonic() - started, 6),
        "log_dir": str(log_dir.relative_to(run_dir)),
    }
    record["validation"] = validate_attempt(record, log_dir, item["input_sha256"])
    if live_errors:
        record["validation"]["critical_errors"].extend(live_errors)
        record["validation"]["passed"] = False
    record["model_calls_succeeded"] = (
        record["validation"]["requests"] == ["talk", "vote"]
        and record["validation"]["usage_count"] == 2
        and not record["validation"]["errors"]
        and not record["validation"]["critical_errors"]
    )
    return record


def make_schedule(
    inputs: list[dict[str, Any]], order_seed: int = ORDER_SEED
) -> list[tuple[dict[str, Any], str, int]]:
    """Create a deterministic, condition-balanced randomized schedule."""
    rng = random.Random(order_seed)
    scenarios = list(inputs)
    rng.shuffle(scenarios)
    schedule: list[tuple[dict[str, Any], str, int]] = []
    for item in scenarios:
        within = [(condition, repetition) for repetition in range(1, REPETITIONS + 1) for condition in CONDITIONS]
        rng.shuffle(within)
        schedule.extend((item, condition, repetition) for condition, repetition in within)
    return schedule


def summarize(run_dir: Path, planned_units: int) -> dict[str, Any]:
    """Write a blinded operational summary from final unit records."""
    records = [json.loads(path.read_text(encoding="utf-8")) for path in (run_dir / "units").glob("*.json")]
    attempts = load_jsonl_if_present(run_dir / "attempts.jsonl")
    calls = sum(int(item["validation"]["call_count"]) for item in attempts)
    usage = sum(int(item["validation"]["usage_count"]) for item in attempts)
    summary = {
        "planned_units": planned_units,
        "completed_units": len(records),
        "attempt_count": len(attempts),
        "observed_call_records": calls,
        "observed_usage_records": usage,
        "generation_successes": sum(bool(record.get("generation_success")) for record in records),
        "model_call_successes": sum(bool(record.get("model_calls_succeeded")) for record in records),
        "validated_units": sum(bool(record.get("validation", {}).get("passed")) for record in records),
        "critical_error_units": sum(bool(record.get("validation", {}).get("critical_errors")) for record in records),
        "provider_models": sorted(
            {model for item in attempts for model in item["validation"].get("response_models", [])},
        ),
        "complete": len(records) == planned_units,
        "updated_at": datetime.now(UTC).astimezone().isoformat(),
    }
    write_json(run_dir / "operational-summary.json", summary)
    return summary


def main() -> int:
    """Run or resume all 480 TALK+VOTE units with bounded parallelism."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-parallel", type=int, default=24)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--corpus-dir", type=Path, default=GENERATED)
    parser.add_argument("--conditions-dir", type=Path, default=ROOT / "experiments" / "pp" / "generated")
    parser.add_argument(
        "--evaluation-spec", type=Path, default=ROOT / "experiments" / "pp" / "evaluation-spec.yml"
    )
    parser.add_argument("--order-seed", type=int, default=ORDER_SEED)
    args = parser.parse_args()
    if args.max_parallel < 1:
        raise ValueError("--max-parallel must be positive")
    repository_status = git("status", "--porcelain")
    if repository_status and not args.allow_dirty:
        raise RuntimeError("Refusing to run analysis from a dirty worktree")

    corpus_dir = args.corpus_dir.resolve()
    conditions_dir = args.conditions_dir.resolve()
    evaluation_spec = args.evaluation_spec.resolve()
    run_dir = ROOT / "experiments" / "runs" / args.run_id
    inputs = read_jsonl(corpus_dir / "inputs.jsonl")
    schedule = make_schedule(inputs, args.order_seed)
    manifest_path = run_dir / "manifest.json"
    if run_dir.exists() and not args.resume:
        raise FileExistsError(f"Refusing to overwrite existing run: {run_dir}")
    if not run_dir.exists() and args.resume:
        raise FileNotFoundError(f"Cannot resume absent run: {run_dir}")

    if not run_dir.exists():
        (run_dir / "units").mkdir(parents=True)
        provenance_dir = run_dir / "provenance"
        provenance_dir.mkdir()
        source_patch = git_bytes("diff", "--binary", "HEAD")
        (provenance_dir / "tracked-source.patch").write_bytes(source_patch)
        (provenance_dir / "runner-snapshot.py").write_bytes(Path(__file__).read_bytes())
        manifest = {
            "schema_version": 1,
            "run_id": args.run_id,
            "purpose": "confirmatory fixed-board generation",
            "exclude_from_confirmatory_analysis": False,
            "created_at": datetime.now(UTC).astimezone().isoformat(),
            "scenario_count": len(inputs),
            "condition_count": len(CONDITIONS),
            "repetitions": REPETITIONS,
            "unit_count": len(schedule),
            "planned_calls": len(schedule) * 2,
            "model": EXPECTED_MODEL,
            "temperature": 1.0,
            "max_parallel": args.max_parallel,
            "order_seed": args.order_seed,
            "corpus_dir": str(corpus_dir.relative_to(ROOT)),
            "conditions_dir": str(conditions_dir.relative_to(ROOT)),
            "evaluation_spec": str(evaluation_spec.relative_to(ROOT)),
            "schedule_sha256": hashlib.sha256(
                canonical_json(
                    [(item["scenario_id"], condition, repetition) for item, condition, repetition in schedule],
                ),
            ).hexdigest(),
            "repository": {
                "commit": git("rev-parse", "HEAD"),
                "branch": git("branch", "--show-current"),
                "dirty": bool(repository_status),
                "status": repository_status.splitlines(),
                "tracked_source_patch_sha256": hashlib.sha256(source_patch).hexdigest(),
                "runner_snapshot_sha256": sha256(provenance_dir / "runner-snapshot.py"),
            },
            "files": {
                "inputs.jsonl": sha256(corpus_dir / "inputs.jsonl"),
                "labels.jsonl": sha256(corpus_dir / "labels.jsonl"),
                "corpus-manifest.json": sha256(corpus_dir / "manifest.json"),
                "evaluation-spec.yml": sha256(evaluation_spec),
                "P+.yml": sha256(conditions_dir / "P+.yml"),
                "P-PP.yml": sha256(conditions_dir / "P-PP.yml"),
                "runner.py": sha256(Path(__file__)),
                "run_confirmation.py": sha256(HERE / "run_confirmation.py"),
                "src/agent/agent.py": sha256(ROOT / "src" / "agent" / "agent.py"),
                "src/agent/possessed.py": sha256(ROOT / "src" / "agent" / "possessed.py"),
                "src/agent/werewolf.py": sha256(ROOT / "src" / "agent" / "werewolf.py"),
                "src/utils/agent_logger.py": sha256(ROOT / "src" / "utils" / "agent_logger.py"),
            },
        }
        write_json(manifest_path, manifest)
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["repository"]["commit"] != git("rev-parse", "HEAD"):
            raise RuntimeError("Refusing to resume from a different repository commit")
        expected_resume_settings = {
            "corpus_dir": str(corpus_dir.relative_to(ROOT)),
            "conditions_dir": str(conditions_dir.relative_to(ROOT)),
            "evaluation_spec": str(evaluation_spec.relative_to(ROOT)),
            "order_seed": args.order_seed,
        }
        for key, value in expected_resume_settings.items():
            if manifest.get(key) != value:
                raise RuntimeError(f"Refusing to resume with changed {key}: {manifest.get(key)!r} != {value!r}")
        for name, expected_hash in manifest["files"].items():
            paths = {
                "inputs.jsonl": corpus_dir / "inputs.jsonl",
                "labels.jsonl": corpus_dir / "labels.jsonl",
                "corpus-manifest.json": corpus_dir / "manifest.json",
                "evaluation-spec.yml": evaluation_spec,
                "P+.yml": conditions_dir / "P+.yml",
                "P-PP.yml": conditions_dir / "P-PP.yml",
                "runner.py": Path(__file__),
                "run_confirmation.py": HERE / "run_confirmation.py",
                "src/agent/agent.py": ROOT / "src" / "agent" / "agent.py",
                "src/agent/possessed.py": ROOT / "src" / "agent" / "possessed.py",
                "src/agent/werewolf.py": ROOT / "src" / "agent" / "werewolf.py",
                "src/utils/agent_logger.py": ROOT / "src" / "utils" / "agent_logger.py",
            }
            if sha256(paths[name]) != expected_hash:
                raise RuntimeError(f"Refusing to resume after file hash changed: {name}")

    complete_ids = {path.stem for path in (run_dir / "units").glob("*.json")}
    pending = [unit for unit in schedule if unit_id(unit[0]["scenario_id"], unit[1], unit[2]) not in complete_ids]
    attempts_by_unit: dict[str, int] = {}
    for previous in load_jsonl_if_present(run_dir / "attempts.jsonl"):
        attempts_by_unit[previous["unit_id"]] = max(
            attempts_by_unit.get(previous["unit_id"], 0),
            int(previous["attempt"]),
        )

    stop_submission = False
    running: dict[Future[dict[str, Any]], tuple[dict[str, Any], str, int, int]] = {}
    with ThreadPoolExecutor(max_workers=min(args.max_parallel, max(len(pending), 1))) as executor:
        while pending or running:
            while pending and not stop_submission and len(running) < args.max_parallel:
                item, condition, repetition = pending.pop(0)
                stable_unit_id = unit_id(item["scenario_id"], condition, repetition)
                attempt = attempts_by_unit.get(stable_unit_id, 0) + 1
                future = executor.submit(
                    run_attempt, run_dir, item, condition, repetition, attempt, conditions_dir
                )
                running[future] = (item, condition, repetition, attempt)
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                item, condition, repetition, attempt = running.pop(future)
                record = future.result()
                attempts_by_unit[record["unit_id"]] = attempt
                append_jsonl(run_dir / "attempts.jsonl", record)
                critical = bool(record["validation"]["critical_errors"])
                retryable_pre_call = (
                    not critical
                    and not record["generation_success"]
                    and record["validation"]["call_count"] == 0
                    and attempt < 2
                )
                if retryable_pre_call:
                    pending.insert(0, (item, condition, repetition))
                    state = "retry-pre-call"
                else:
                    write_json(run_dir / "units" / f"{record['unit_id']}.json", record)
                    state = "validated" if record["validation"]["passed"] else "retained-failure"
                if critical:
                    stop_submission = True
                    state = "critical-stop"
                summary = summarize(run_dir, len(schedule))
                print(
                    f"{record['unit_id']} attempt={attempt} state={state} "
                    f"calls={record['validation']['call_count']} completed={summary['completed_units']}/{len(schedule)}",
                    flush=True,
                )

    summary = summarize(run_dir, len(schedule))
    if stop_submission:
        print("Stopped new submissions after a critical validation error", file=sys.stderr)
        return 2
    if not summary["complete"]:
        print("Run is incomplete; resume with --resume", file=sys.stderr)
        return 1
    print(
        f"Completed {summary['completed_units']} units; calls={summary['observed_call_records']}; "
        f"validated={summary['validated_units']}; provider_models={summary['provider_models']}",
    )
    return 0 if summary["validated_units"] == len(schedule) else 1


if __name__ == "__main__":
    raise SystemExit(main())
