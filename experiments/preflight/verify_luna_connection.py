#!/usr/bin/env python3
"""Verify a five-client Luna connection run and save secret-free evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "experiments" / "validation" / "pp-luna-connection.json"
EXPECTED_MODEL = "gpt-5.6-luna"
EXPECTED_TEMPERATURE = "1.0"


def records(path: Path) -> list[dict[str, Any]]:
    """Read JSONL mappings."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    """Return a file digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Check process completion and every provider-reported response model."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()

    finishes = [row for row in records(run_dir / "execution.jsonl") if row.get("event") == "finish"]
    if len(finishes) != 1:
        raise ValueError(f"Expected one finish event, found {len(finishes)}")
    finish = finishes[0]
    game_id = str(finish.get("game_id", ""))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    game = summary["games"][game_id]

    log_dirs = [run_dir / "agent-logs" / "P+"]
    log_dirs.extend(sorted((run_dir / "agent-logs" / "opponents").glob("*")))
    agents: dict[str, dict[str, Any]] = {}
    evidence: dict[str, str] = {}
    non_english_violations = 0
    for log_dir in log_dirs:
        call_path = log_dir / "llm_calls.jsonl"
        usage_path = log_dir / "llm_usage.jsonl"
        violation_path = log_dir / "rule_violations.jsonl"
        evidence[str(call_path.relative_to(run_dir))] = sha256(call_path)
        evidence[str(usage_path.relative_to(run_dir))] = sha256(usage_path)
        if violation_path.is_file():
            evidence[str(violation_path.relative_to(run_dir))] = sha256(violation_path)
            non_english_violations += sum(
                "NON_ENGLISH_TEXT" in row.get("violations", [])
                for row in records(violation_path)
                if row.get("game_id") == game_id
            )
        for row in records(call_path):
            if row.get("game_id") != game_id:
                continue
            agent = str(row["agent"])
            item = agents.setdefault(
                agent,
                {
                    "calls": 0,
                    "failed_calls": 0,
                    "observable_retries": 0,
                    "provider_model_ids": set(),
                    "configured_temperatures": set(),
                    "effective_temperatures": set(),
                },
            )
            item["calls"] += 1
            item["failed_calls"] += int(row.get("success") is not True)
            item["observable_retries"] += int(row.get("retry_count_observable") or 0)
            response_model = row.get("response_model_id")
            if isinstance(response_model, str) and response_model:
                item["provider_model_ids"].add(response_model)
            model = row.get("model")
            if isinstance(model, dict):
                item["configured_temperatures"].add(str(model.get("temperature", "")))
                item["effective_temperatures"].add(str(model.get("effective_temperature", "")))

    checks = {
        "not_timed_out": finish.get("timed_out") is False,
        "target_exited_zero": finish.get("agent_returncode") == 0,
        "four_opponents_exited_zero": finish.get("opponent_returncodes") == [0, 0, 0, 0],
        "one_server_game": finish.get("game_log_count") == 1,
        "five_agents_logged": len(agents) == 5,
        "all_calls_succeeded": all(item["failed_calls"] == 0 for item in agents.values()),
        "no_observable_retries": all(item["observable_retries"] == 0 for item in agents.values()),
        "all_provider_models_luna": all(item["provider_model_ids"] == {EXPECTED_MODEL} for item in agents.values()),
        "all_configured_temperatures_frozen": all(
            item["configured_temperatures"] == {EXPECTED_TEMPERATURE} for item in agents.values()
        ),
        "all_effective_temperatures_frozen": all(
            item["effective_temperatures"] == {EXPECTED_TEMPERATURE} for item in agents.values()
        ),
        "no_non_english_outputs": non_english_violations == 0,
    }
    serializable_agents = {
        agent: {
            **item,
            "provider_model_ids": sorted(item["provider_model_ids"]),
            "configured_temperatures": sorted(item["configured_temperatures"]),
            "effective_temperatures": sorted(item["effective_temperatures"]),
        }
        for agent, item in sorted(agents.items())
    }
    report = {
        "schema_version": 2,
        "run_id": run_dir.name,
        "game_id": game_id,
        "actual_target_role": finish.get("actual_role"),
        "checks": checks,
        "agents": serializable_agents,
        "totals": {
            "calls": game["calls"],
            "failed_calls": game["failed_calls"],
            "observable_retries": game["observable_retries"],
            "non_english_violations": non_english_violations,
            "input_tokens": game["input_tokens"],
            "cached_input_tokens": game["cached_input_tokens"],
            "output_tokens": game["output_tokens"],
            "estimated_cost_usd": game["estimated_cost_usd"],
        },
        "evidence_sha256": dict(sorted(evidence.items())),
        "passed": all(checks.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"passed={report['passed']} game_id={game_id} agents={len(agents)} "
        f"calls={game['calls']} cost_usd={game['estimated_cost_usd']}",
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
