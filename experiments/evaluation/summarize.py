#!/usr/bin/env python3
"""Create a blinded operational pilot summary or an explicitly unblinded result."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

VILLAGE_ROLES = {"VILLAGER", "SEER", "MEDIUM", "BODYGUARD"}
WOLF_ROLES = {"WEREWOLF", "POSSESSED"}


def records(path: Path) -> list[dict[str, Any]]:
    """Read valid JSON objects from a JSONL file."""
    if not path.is_file():
        return []
    output = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            output.append(value)
    return output


def token_value(usage: dict[str, Any], names: tuple[str, ...]) -> int:
    """Return the first provider token field that exists."""
    for name in names:
        value = usage.get(name)
        if isinstance(value, int):
            return value
    return 0


def won_game(role: str, win_side: str) -> bool | None:
    """Map the target role and server winning side to a win flag."""
    side = win_side.upper()
    if role in VILLAGE_ROLES:
        return side in {"VILLAGER", "VILLAGE", "HUMAN"}
    if role in WOLF_ROLES:
        return side in {"WEREWOLF", "WOLF"}
    return None


def main() -> int:
    """Aggregate outcomes, role balance, guard audit, tokens, and estimated cost."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--unblind",
        action="store_true",
        help="include wins and directional guard outcomes after the Stage 2 specification is frozen",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    plan = yaml.safe_load((run_dir / "plan.yml").read_text(encoding="utf-8"))
    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        assignments = list(csv.DictReader(handle))

    summary: dict[str, dict[str, Any]] = {
        condition: {
            "planned_games": 0,
            "completed_games": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "roles": Counter(),
            "calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "mean_latency_seconds": None,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "audited_outputs": 0,
            "outputs_with_violations": 0,
            "corrected_outputs": 0,
            "raw_passthrough_mismatches": 0,
        }
        for condition in plan["conditions"]
    }
    warnings: list[str] = []
    latencies: dict[str, list[float]] = defaultdict(list)
    target_team = plan["target_team"]
    game_rows: dict[str, dict[str, str]] = {}

    for row in assignments:
        condition = row["condition"]
        summary[condition]["planned_games"] += 1
        json_dir = run_dir / "server-logs" / row["row_id"] / "json"
        game_files = sorted(json_dir.glob("*.json")) if json_dir.is_dir() else []
        if len(game_files) != 1:
            warnings.append(f"{row['row_id']}: expected one server JSON, found {len(game_files)}")
            continue
        game = json.loads(game_files[0].read_text(encoding="utf-8"))
        game_id = str(game.get("game_id", ""))
        if game_id:
            game_rows[game_id] = row
        agents = [agent for agent in game.get("agents", []) if agent.get("team") == target_team]
        if len(agents) != 1:
            warnings.append(f"{row['row_id']}: expected one {target_team} agent, found {len(agents)}")
            continue
        role = str(agents[0].get("role", "")).upper()
        summary[condition]["completed_games"] += 1
        summary[condition]["roles"][role] += 1
        won = won_game(role, str(game.get("win_side", "")))
        if won is True:
            summary[condition]["wins"] += 1
        elif won is False:
            summary[condition]["losses"] += 1
        else:
            warnings.append(f"{row['row_id']}: cannot map role={role}, win_side={game.get('win_side')}")
        if row["planned_role"] != role:
            warnings.append(f"{row['row_id']}: planned {row['planned_role']}, observed {role}")

    prices = plan["pricing_usd_per_million_tokens"]["standard"]["short_context"]
    for condition in summary:
        condition_dir = run_dir / "agent-logs" / condition
        call_paths = sorted(condition_dir.rglob("llm_calls.jsonl")) if condition_dir.is_dir() else []
        for call_path in call_paths:
            for record in records(call_path):
                item = summary[condition]
                item["calls"] += 1
                if record.get("success") is True:
                    item["successful_calls"] += 1
                else:
                    item["failed_calls"] += 1
                latency = record.get("latency_seconds")
                if isinstance(latency, (int, float)):
                    latencies[condition].append(float(latency))

        usage_paths = sorted(condition_dir.rglob("llm_usage.jsonl")) if condition_dir.is_dir() else []
        for usage_path in usage_paths:
            for record in records(usage_path):
                usage = record.get("usage", {})
                if not isinstance(usage, dict):
                    continue
                input_tokens = token_value(usage, ("input_tokens", "prompt_tokens"))
                cached_tokens = token_value(
                    usage,
                    (
                        "input_token_details.cache_read",
                        "input_token_details.cached_tokens",
                        "prompt_tokens_details.cached_tokens",
                    ),
                )
                output_tokens = token_value(usage, ("output_tokens", "completion_tokens"))
                uncached_tokens = max(0, input_tokens - cached_tokens)
                item = summary[condition]
                item["input_tokens"] += input_tokens
                item["cached_input_tokens"] += cached_tokens
                item["output_tokens"] += output_tokens
                item["estimated_cost_usd"] += (
                    uncached_tokens * prices["input"]
                    + cached_tokens * prices["cached_input"]
                    + output_tokens * prices["output"]
                ) / 1_000_000

        audit_paths = sorted(condition_dir.rglob("rule_violations.jsonl")) if condition_dir.is_dir() else []
        for audit_path in audit_paths:
            for record in records(audit_path):
                item = summary[condition]
                item["audited_outputs"] += 1
                if record.get("violations"):
                    item["outputs_with_violations"] += 1
                if record.get("guarded"):
                    item["corrected_outputs"] += 1
                if (
                    condition == "G"
                    and str(record.get("raw_llm_response", "")).strip() != str(record.get("final_response", "")).strip()
                ):
                    item["raw_passthrough_mismatches"] += 1

    game_usage: dict[str, dict[str, Any]] = {
        game_id: {
            "row_id": row["row_id"],
            "condition": row["condition"],
            "agents": {},
            "calls": 0,
            "failed_calls": 0,
            "observable_retries": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
        for game_id, row in game_rows.items()
    }
    log_root = run_dir / "agent-logs"
    for call_path in sorted(log_root.rglob("llm_calls.jsonl")) if log_root.is_dir() else []:
        log_dir = call_path.parent
        for record in records(call_path):
            game_id = str(record.get("game_id", ""))
            if game_id not in game_usage:
                warnings.append(f"unjoined llm call game_id={game_id!r} from {log_dir}")
                continue
            agent = str(record.get("agent", ""))
            game = game_usage[game_id]
            agent_item = game["agents"].setdefault(
                agent,
                {"calls": 0, "failed_calls": 0, "observable_retries": 0, "model_ids": []},
            )
            agent_item["calls"] += 1
            game["calls"] += 1
            if record.get("success") is not True:
                agent_item["failed_calls"] += 1
                game["failed_calls"] += 1
            retries = record.get("retry_count_observable")
            if isinstance(retries, int):
                agent_item["observable_retries"] += retries
                game["observable_retries"] += retries
            model = record.get("model", {})
            configured = model.get("model") if isinstance(model, dict) else None
            response_model = record.get("response_model_id")
            for model_id in (configured, response_model):
                if isinstance(model_id, str) and model_id and model_id not in agent_item["model_ids"]:
                    agent_item["model_ids"].append(model_id)
    for usage_path in sorted(log_root.rglob("llm_usage.jsonl")) if log_root.is_dir() else []:
        log_dir = usage_path.parent
        for record in records(usage_path):
            game_id = str(record.get("game_id", ""))
            if game_id not in game_usage:
                warnings.append(f"unjoined llm usage game_id={game_id!r} from {log_dir}")
                continue
            usage = record.get("usage", {})
            if not isinstance(usage, dict):
                continue
            input_tokens = token_value(usage, ("input_tokens", "prompt_tokens"))
            cached_tokens = token_value(
                usage,
                (
                    "input_token_details.cache_read",
                    "input_token_details.cached_tokens",
                    "prompt_tokens_details.cached_tokens",
                ),
            )
            output_tokens = token_value(usage, ("output_tokens", "completion_tokens"))
            uncached_tokens = max(0, input_tokens - cached_tokens)
            cost = (
                uncached_tokens * prices["input"]
                + cached_tokens * prices["cached_input"]
                + output_tokens * prices["output"]
            ) / 1_000_000
            game = game_usage[game_id]
            game["input_tokens"] += input_tokens
            game["cached_input_tokens"] += cached_tokens
            game["output_tokens"] += output_tokens
            game["estimated_cost_usd"] += cost

    for game_id, game in game_usage.items():
        game["estimated_cost_usd"] = round(game["estimated_cost_usd"], 6)
        if len(game["agents"]) != 5:
            warnings.append(f"{game_id}: expected usage/call logs for five agents, found {len(game['agents'])}")
        for agent_item in game["agents"].values():
            agent_item["model_ids"].sort()

    directional_fields = {
        "wins",
        "losses",
        "win_rate",
        "outputs_with_violations",
        "corrected_outputs",
    }
    serializable: dict[str, dict[str, Any]] = {}
    for condition, item in summary.items():
        decided = item["wins"] + item["losses"]
        item["win_rate"] = item["wins"] / decided if decided else None
        item["roles"] = dict(sorted(item["roles"].items()))
        item["estimated_cost_usd"] = round(item["estimated_cost_usd"], 6)
        if latencies[condition]:
            item["mean_latency_seconds"] = round(sum(latencies[condition]) / len(latencies[condition]), 6)
        serializable[condition] = {
            key: value for key, value in item.items() if args.unblind or key not in directional_fields
        }

    result = {
        "unblinded": args.unblind,
        "warning": (
            "Descriptive pilot result; do not perform or report significance tests."
            if args.unblind
            else "Blinded operational pilot summary; directional outcome fields are withheld."
        ),
        "conditions": serializable,
        "games": game_usage,
        "warnings": warnings,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    columns = ["condition", *next(iter(serializable.values())).keys()]
    with (run_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for condition, item in serializable.items():
            writer.writerow({"condition": condition, **item, "roles": json.dumps(item["roles"], sort_keys=True)})
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
