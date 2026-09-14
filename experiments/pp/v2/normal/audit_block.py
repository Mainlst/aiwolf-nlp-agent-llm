#!/usr/bin/env python3
"""Blind, fail-closed audit for one 20-game PP normal-game block."""

# ruff: noqa: C901, D103, PLR0912, PLR0915, PLR2004, T201

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def usage_cost(record: dict[str, Any], prices: dict[str, float]) -> tuple[int, int, int, float]:
    usage = record.get("usage", {})
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
    cached_tokens = int(
        usage.get("input_token_details.cache_read", usage.get("prompt_tokens_details.cached_tokens", 0)),
    )
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    cost = (
        max(0, input_tokens - cached_tokens) * prices["input"]
        + cached_tokens * prices["cached_input"]
        + output_tokens * prices["output"]
    ) / 1_000_000
    return input_tokens, cached_tokens, output_tokens, cost


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("analysis_block")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    block = f"{int(args.analysis_block):02d}"
    plan = yaml.safe_load((run_dir / "plan.yml").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["analysis_block"] == block]
    failures: list[str] = []
    balance_field = "format" if rows and rows[0].get("format") else "condition"
    expected_levels = ("turn_based", "anytime") if balance_field == "format" else ("P+", "P-PP")
    if len(rows) != 20 or Counter(row[balance_field] for row in rows) != Counter(
        dict.fromkeys(expected_levels, 10),
    ):
        failures.append("allocation is not a balanced 20-game block")

    finishes = {
        record["row_id"]: record
        for record in jsonl(run_dir / "execution.jsonl")
        if record.get("event") == "finish" and record.get("row_id") in {row["row_id"] for row in rows}
    }
    expected_teams = {plan["target_team"], *plan["opponent_O"]["agents"]}
    expected_model = plan["fixed_runtime"]["model"]
    prices = plan["pricing_usd_per_million_tokens"]["standard"]["short_context"]
    game_ids: set[str] = set()
    model_ids: Counter[str] = Counter()
    input_tokens = cached_tokens = output_tokens = 0
    estimated_cost = 0.0
    calls = 0
    row_audits: list[dict[str, Any]] = []

    for row in rows:
        row_id = row["row_id"]
        reasons: list[str] = []
        finish = finishes.get(row_id)
        if finish is None:
            reasons.append("missing_finish_event")
            row_audits.append({"row_id": row_id, "eligible": False, "reasons": reasons})
            failures.append(f"{row_id}: missing finish event")
            continue
        if finish.get("timed_out"):
            reasons.append("timed_out")
        finish_confirmed = finish.get("client_finish_confirmed", [False] * 5)
        returncodes = [finish.get("agent_returncode"), *finish.get("opponent_returncodes", [])]
        if len(finish_confirmed) != 5 or any(
            code != 0 and not confirmed
            for code, confirmed in zip(returncodes, finish_confirmed, strict=False)
        ):
            reasons.append("client_failure")
        if finish.get("server_returncode_after_termination") != 0:
            reasons.append("server_failure")
        if finish.get("game_log_count") != 1:
            reasons.append("server_log_count")
        if finish.get("actual_role") != row["planned_role"]:
            reasons.append("target_role_mismatch")
        if str(finish.get("actual_seat")) != row["target_seat"]:
            reasons.append("target_seat_mismatch")
        roster = finish.get("roster", [])
        if len(roster) != 5 or {agent.get("team") for agent in roster} != expected_teams:
            reasons.append("roster_mismatch")
        seed_path = run_dir / "optimizer-seeds" / f"{row_id}.json"
        frozen_hash = manifest.get("files", {}).get(str(seed_path.relative_to(run_dir)))
        if not seed_path.is_file() or not frozen_hash or sha256(seed_path) != frozen_hash:
            reasons.append("optimizer_seed_hash_mismatch")
        else:
            seed = json.loads(seed_path.read_text(encoding="utf-8"))
            scheduled = seed.get("scheduled_matches", [])
            if len(scheduled) != 1:
                reasons.append("optimizer_schedule_mismatch")
            else:
                expected_by_seat: dict[int, tuple[str, str]] = {}
                for role, indexes in scheduled[0].get("role_idxs", {}).items():
                    for index in indexes:
                        expected_by_seat[int(index) + 1] = (seed["idx_team_map"][str(index)], role)
                observed_by_seat = {
                    int(agent["idx"]): (str(agent["team"]), str(agent["role"])) for agent in roster
                }
                if observed_by_seat != expected_by_seat:
                    reasons.append("full_seat_role_assignment_mismatch")
        if row.get("format"):
            server_config_path = run_dir / "server-configs" / f"{row_id}.yml"
            server_config_hash = manifest.get("files", {}).get(str(server_config_path.relative_to(run_dir)))
            if (
                not server_config_path.is_file()
                or not server_config_hash
                or sha256(server_config_path) != server_config_hash
            ):
                reasons.append("frozen_server_config_hash_mismatch")
                server_config = None
            else:
                server_config = yaml.safe_load(server_config_path.read_text(encoding="utf-8"))
        else:
            server_config = None
        if server_config is not None:
            duration_present = "duration" in server_config["game"]["talk"]
            if duration_present != (row["format"] == "anytime"):
                reasons.append("communication_format_mismatch")
        game_id = str(finish.get("game_id", ""))
        if not game_id or game_id in game_ids:
            reasons.append("missing_or_duplicate_game_id")
        game_ids.add(game_id)

        row_calls = 0
        row_agents: set[str] = set()
        row_model_ids: set[str] = set()
        attempt_id = str(finish.get("attempt_id", ""))
        if not attempt_id:
            reasons.append("missing_attempt_id")
        log_dirs = [run_dir / "agent-logs" / row["condition"] / row_id / attempt_id]
        log_dirs.extend(
            run_dir / "agent-logs" / "opponents" / team / row_id / attempt_id
            for team in plan["opponent_O"]["agents"]
        )
        for log_dir in log_dirs:
            for record in jsonl(log_dir / "llm_calls.jsonl"):
                if record.get("game_id") != game_id:
                    continue
                row_calls += 1
                calls += 1
                row_agents.add(str(record.get("agent", "")))
                configured = record.get("model", {})
                if isinstance(configured, dict):
                    row_model_ids.add(str(configured.get("model", "")))
                if record.get("response_model_id"):
                    row_model_ids.add(str(record["response_model_id"]))
            for record in jsonl(log_dir / "llm_usage.jsonl"):
                if record.get("game_id") != game_id:
                    continue
                configured = record.get("model", {})
                if isinstance(configured, dict):
                    row_model_ids.add(str(configured.get("model", "")))
                if record.get("response_model_id"):
                    row_model_ids.add(str(record["response_model_id"]))
                i, c, o, cost = usage_cost(record, prices)
                input_tokens += i
                cached_tokens += c
                output_tokens += o
                estimated_cost += cost
        row_model_ids.discard("")
        model_ids.update(row_model_ids)
        if len(row_agents) != 5 or row_calls == 0:
            reasons.append("incomplete_five_agent_usage")
        if row_model_ids != {expected_model}:
            reasons.append("unintended_model")
        frozen_configs = [run_dir / "agent-configs" / f"{row['condition']}.yml"]
        frozen_configs.extend(run_dir / "opponent-configs" / f"{team}.yml" for team in plan["opponent_O"]["agents"])
        for config_path in frozen_configs:
            relative = str(config_path.relative_to(run_dir))
            expected_hash = manifest.get("files", {}).get(relative)
            if not expected_hash or sha256(config_path) != expected_hash:
                reasons.append("frozen_config_hash_mismatch")
                break
        if reasons:
            failures.extend(f"{row_id}: {reason}" for reason in sorted(set(reasons)))
        row_audits.append({"row_id": row_id, "eligible": not reasons, "reasons": sorted(set(reasons))})

    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "analysis_block": block,
        "blinded": True,
        "condition_specific_outcomes_included": False,
        "planned_games": len(rows),
        "finished_games": len(finishes),
        "unique_game_ids": len(game_ids),
        "eligible_games": sum(item["eligible"] for item in row_audits),
        "models": dict(model_ids),
        "calls": calls,
        "tokens": {
            "input": input_tokens,
            "cached_input": cached_tokens,
            "output": output_tokens,
        },
        "estimated_cost_usd": round(estimated_cost, 6),
        "passed": not failures,
        "failures": failures,
        "rows": row_audits,
    }
    output_dir = run_dir / "block-audits"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"block-{block}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))
    print(f"report={output} sha256={sha256(output)}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
