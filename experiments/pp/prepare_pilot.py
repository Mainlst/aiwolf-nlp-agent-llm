#!/usr/bin/env python3
"""Create an immutable-by-convention P+/P-PP pilot run without API calls."""

# ruff: noqa: C901, EM101, EM102, PLR0912, PLR0915, S603, S607, T201, TRY003

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "experiments" / "pp" / "pilot"
GENERATED = ROOT / "experiments" / "pp" / "generated"


def load_mapping(path: Path) -> dict[str, Any]:
    """Load a YAML mapping."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected mapping: {path}")
    return value


def sha256(path: Path) -> str:
    """Return a file SHA-256 digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_server_binary(plan: dict[str, Any], server_binary: Path) -> None:
    """Reject a server binary that differs from a plan-pinned artifact."""
    expected = plan.get("required_server_binary_sha256")
    if expected is None:
        return
    actual = sha256(server_binary)
    if actual != str(expected):
        raise ValueError(
            "Server binary SHA-256 does not match required_server_binary_sha256: "
            f"expected={expected} actual={actual}",
        )


def git(*args: str, cwd: Path = ROOT) -> str:
    """Run one read-only Git query."""
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def dump_yaml(path: Path, value: dict[str, Any]) -> None:
    """Write stable, human-readable YAML."""
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def agent_config(
    condition: str,
    plan: dict[str, Any],
    run_dir: Path,
    generated_dir: Path = GENERATED,
) -> dict[str, Any]:
    """Resolve one frozen generated condition for this run."""
    config = load_mapping(generated_dir / f"{condition}.yml")
    config["web_socket"]["url"] = plan["server_url"]
    config["web_socket"]["auto_reconnect"] = False
    config["agent"]["num"] = plan["target_agent_count"]
    config["agent"]["team"] = plan["target_team"]
    config["log"]["output_dir"] = str(run_dir / "agent-logs" / condition)
    config["log"]["console_output"] = True
    config["log"]["file_output"] = True
    return config


def opponent_config(team: str, plan: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Build one condition-independent Luna opponent config."""
    opponent = plan["opponent_O"]
    config = load_mapping(ROOT / opponent["source_config"])
    config["web_socket"]["url"] = plan["server_url"]
    config["web_socket"]["auto_reconnect"] = False
    config["agent"]["num"] = 1
    config["agent"]["team"] = team
    config["openai"].update(
        {
            "model": opponent["model"],
            "temperature": opponent["temperature"],
            "max_retries": opponent["max_retries"],
        },
    )
    config["experiment"] = {
        "study_id": plan["study_id"],
        "opponent_id": opponent["id"],
        "condition_independent": True,
    }
    config["log"]["output_dir"] = str(run_dir / "agent-logs" / "opponents" / team)
    config["log"]["console_output"] = True
    config["log"]["file_output"] = True
    return config


def opponent_behavior_hashes(config: dict[str, Any]) -> dict[str, str]:
    """Hash the condition-independent behavior surface, excluding identity and paths."""
    canonical = deepcopy(config)
    canonical["web_socket"] = {"url": "<server>", "token": None, "auto_reconnect": False}
    canonical["agent"]["team"] = "<opponent-team>"
    canonical["log"]["output_dir"] = "<opponent-log>"

    def mapping_hash(value: object) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    return {
        "functional_config": mapping_hash(canonical),
        "model": mapping_hash(canonical["openai"]),
        "expanded_prompts": mapping_hash(canonical["prompt"]),
        "normal_strategy": mapping_hash(canonical["strategy"]),
        "pp_response": mapping_hash(
            {
                "strategy": canonical["strategy"],
                "prompt": canonical["prompt"],
                "runtime_source_sha256": sha256(ROOT / "src" / "agent" / "agent.py"),
            },
        ),
    }


def optimizer_seed(row: dict[str, str], registry: dict[str, Any]) -> dict[str, Any]:
    """Build a one-match optimizer state that fixes the target agent's role."""
    if row["opponent_pool"] != "O":
        raise ValueError(f"Paid PP runs require fixed opponent O, got {row['opponent_pool']}")
    opponents = list(registry["runtime_opponent"]["agents"])
    target_index = int(row["target_seat"]) - 1
    teams = list(opponents)
    teams.insert(target_index, "yatolab")
    idx_team_map = {str(index): team for index, team in enumerate(teams)}
    target_role = row["planned_role"]
    other_wolf_role = "WEREWOLF" if target_role == "POSSESSED" else "POSSESSED"
    other_indices = [index for index in range(5) if index != target_index]
    role_idxs = {
        target_role: [target_index],
        other_wolf_role: [other_indices[0]],
        "SEER": [other_indices[1]],
        "VILLAGER": other_indices[2:],
    }
    return {
        "infinite_loop": False,
        "team_count": 5,
        "game_count": 1,
        "idx_team_map": idx_team_map,
        "role_num_map": {"BODYGUARD": 0, "MEDIUM": 0, "POSSESSED": 1, "SEER": 1, "VILLAGER": 2, "WEREWOLF": 1},
        "ended_matches": [],
        "scheduled_matches": [{"role_idxs": role_idxs, "weight": 1.0}],
    }


def server_config(base: dict[str, Any], row: dict[str, str], run_dir: Path) -> dict[str, Any]:
    """Resolve one isolated one-game server config."""
    config = deepcopy(base)
    config["server"]["web_socket"].update({"host": "127.0.0.1", "port": 8080})
    config["matching"].update(
        {
            "self_match": False,
            "is_optimize": True,
            "team_count": 5,
            "game_count": 1,
            "infinite_loop": False,
            "output_path": str(run_dir / "optimizer-seeds" / f"{row['row_id']}.json"),
        },
    )
    row_log = run_dir / "server-logs" / row["row_id"]
    config["json_logger"].update({"enable": True, "output_dir": str(row_log / "json"), "filename": "{game_id}"})
    config["game_logger"].update({"enable": True, "output_dir": str(row_log / "game"), "filename": "{game_id}"})
    if "realtime_broadcaster" in config:
        config["realtime_broadcaster"].update(
            {"enable": True, "output_dir": str(row_log / "realtime"), "filename": "{game_id}"},
        )
    return config


def main() -> int:
    """Create resolved configs, optimizer seeds, protocol copies, and manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--server-config", type=Path)
    parser.add_argument("--server-binary", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--plan", type=Path, default=PILOT / "plan.yml")
    parser.add_argument("--assignments", type=Path, default=PILOT / "assignments.csv")
    parser.add_argument("--opponent-registry", type=Path, default=PILOT / "opponent-pools.yml")
    parser.add_argument("--conditions-dir", type=Path, default=GENERATED)
    args = parser.parse_args()

    run_dir = ROOT / "experiments" / "runs" / args.run_id
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing run: {run_dir}")
    status = git("status", "--porcelain")
    if args.require_clean and status:
        raise RuntimeError("Refusing to prepare a frozen pilot from a dirty worktree")

    plan_path = args.plan.resolve()
    assignments_path = args.assignments.resolve()
    registry_path = args.opponent_registry.resolve()
    conditions_dir = args.conditions_dir.resolve()
    plan = load_mapping(plan_path)
    registry = load_mapping(registry_path)
    if args.server_config:
        server_base_paths = {"all": args.server_config.resolve()}
    elif "base_server_configs" in plan:
        server_base_paths = {
            str(mode): (ROOT / relative).resolve()
            for mode, relative in plan["base_server_configs"].items()
        }
    else:
        server_base_paths = {"all": (ROOT / plan["base_server_config"]).resolve()}
    server_binary = (args.server_binary or (ROOT.parent / "aiwolf-nlp-server-linux-amd64")).resolve()
    server_repos = {path.parents[1] for path in server_base_paths.values()}
    if len(server_repos) != 1:
        raise ValueError("All base server configs must belong to one server repository")
    server_repo = server_repos.pop()
    server_status = git("status", "--porcelain", cwd=server_repo) if (server_repo / ".git").is_dir() else ""
    if args.require_clean and server_status:
        raise RuntimeError("Refusing to prepare with a dirty AIWolf server worktree")
    if not all(path.is_file() for path in server_base_paths.values()) or not server_binary.is_file():
        raise FileNotFoundError("Server config or frozen server binary is missing")
    validate_server_binary(plan, server_binary)
    with assignments_path.open(encoding="utf-8", newline="") as handle:
        assignments = list(csv.DictReader(handle))

    for relative in (
        "agent-configs",
        "opponent-configs",
        "server-configs",
        "agent-logs",
        "server-logs",
        "events",
        "optimizer-seeds",
        "server",
        "protocol",
        "protocol/runtime",
        "protocol/stage0-qualification",
        "protocol/validation",
    ):
        (run_dir / relative).mkdir(parents=True, exist_ok=False)
    for source, destination in (
        (plan_path, "plan.yml"),
        (assignments_path, "assignments.csv"),
        (registry_path, "opponent-pools.yml"),
    ):
        shutil.copy2(source, run_dir / destination)
    for source in (
        "check_pilot.py",
        "prepare_pilot.py",
        "check_conditions.py",
        "generate_conditions.py",
        "validate_environment.py",
    ):
        shutil.copy2(ROOT / "experiments" / "pp" / source, run_dir / "protocol" / source)
    condition_spec = (ROOT / plan["condition_spec"]).resolve()
    evaluation_spec = (ROOT / plan["evaluation_spec"]).resolve()
    shutil.copy2(condition_spec, run_dir / "protocol" / "condition-spec.yml")
    shutil.copy2(evaluation_spec, run_dir / "protocol" / "evaluation-spec.yml")
    shutil.copy2(conditions_dir / "manifest.json", run_dir / "protocol" / "condition-manifest.json")
    for source in (
        "runtime/run_one.py",
        "runtime/run_parallel.py",
        "runtime/opponents.py",
        "evaluation/pp_log_parser.py",
        "evaluation/verify_pp_saved_log.py",
        "evaluation/verify_saved_logs.py",
        "preflight/verify_openai_access.py",
        "preflight/verify_luna_connection.py",
        "preflight/estimate_budget.py",
        "evaluation/summarize.py",
    ):
        shutil.copy2(ROOT / "experiments" / source, run_dir / "protocol" / Path(source).name)
    for source in ("audit_block.py", "backup_block.py", "finalize_eligibility.py", "run_blocks.py"):
        shutil.copy2(ROOT / "experiments" / "pp" / "v2" / "normal" / source, run_dir / "protocol" / source)
    for source in plan.get("protocol_sources", []):
        source_path = (ROOT / source).resolve()
        shutil.copy2(source_path, run_dir / "protocol" / source_path.name)
    for source in ("src/main.py", "src/agent/agent.py", "src/utils/agent_logger.py"):
        source_path = ROOT / source
        shutil.copy2(source_path, run_dir / "protocol" / "runtime" / source.replace("/", "-"))
    shutil.copy2(ROOT / registry["qualification_report"], run_dir / "protocol" / "stage0-qualification" / "report.json")
    for raw_log in registry["qualification_raw_logs"]:
        source_path = ROOT / raw_log["file"]
        shutil.copy2(source_path, run_dir / "protocol" / "stage0-qualification" / source_path.name)
    for source in (
        "saved-log-verification.json",
        "pp-saved-log-verification.json",
        "openai-access.json",
        "pp-environment-preflight.json",
        "pp-luna-connection.json",
        "experiment-budget-estimate.json",
    ):
        source_path = ROOT / "experiments" / "validation" / source
        if source_path.is_file():
            shutil.copy2(source_path, run_dir / "protocol" / "validation" / source)
    for mode, server_base_path in server_base_paths.items():
        destination = "base-server-config.yml" if mode == "all" else f"base-server-config-{mode}.yml"
        shutil.copy2(server_base_path, run_dir / destination)
    source_patch = git("diff", "--binary", "HEAD")
    (run_dir / "source.patch").write_text(source_patch + ("\n" if source_patch else ""), encoding="utf-8")
    server_source_patch = git("diff", "--binary", "HEAD", cwd=server_repo) if (server_repo / ".git").is_dir() else ""
    (run_dir / "protocol" / "server-source.patch").write_text(
        server_source_patch + ("\n" if server_source_patch else ""),
        encoding="utf-8",
    )

    for condition in plan["conditions"]:
        dump_yaml(
            run_dir / "agent-configs" / f"{condition}.yml",
            agent_config(condition, plan, run_dir, conditions_dir),
        )
    opponent_hashes: dict[str, dict[str, str]] = {}
    for team in plan["opponent_O"]["agents"]:
        config = opponent_config(team, plan, run_dir)
        dump_yaml(run_dir / "opponent-configs" / f"{team}.yml", config)
        opponent_hashes[team] = opponent_behavior_hashes(config)
    for row in assignments:
        mode = "all" if "all" in server_base_paths else row["format"]
        if mode not in server_base_paths:
            raise ValueError(f"Unknown server format {mode!r} for {row['row_id']}")
        base_server = load_mapping(server_base_paths[mode])
        dump_yaml(run_dir / "server-configs" / f"{row['row_id']}.yml", server_config(base_server, row, run_dir))
        (run_dir / "optimizer-seeds" / f"{row['row_id']}.json").write_text(
            json.dumps(optimizer_seed(row, registry), ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    tracked = sorted(path for path in run_dir.rglob("*") if path.is_file())
    manifest = {
        "schema_version": 1,
        "study_id": plan["study_id"],
        "run_id": args.run_id,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "repository": {
            "commit": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "git_dirty": bool(status),
            "status": status,
        },
        "server": {
            "base_config": str(server_base_paths["all"]) if "all" in server_base_paths else None,
            "base_config_sha256": sha256(server_base_paths["all"]) if "all" in server_base_paths else None,
            "base_configs": {
                mode: {"path": str(path), "sha256": sha256(path)}
                for mode, path in server_base_paths.items()
            },
            "binary": str(server_binary),
            "binary_sha256": sha256(server_binary),
            "repository": str(server_repo),
            "repository_commit": git("rev-parse", "HEAD", cwd=server_repo) if (server_repo / ".git").is_dir() else None,
            "repository_dirty": bool(server_status),
            "repository_status": server_status,
            "source_patch_sha256": sha256(run_dir / "protocol" / "server-source.patch"),
        },
        "allocation": {
            "target_role_is_forced_by_per_row_optimizer_seed": True,
            "target_seat_is_forced_by_per_row_optimizer_seed": True,
        },
        "sources": {
            "plan": str(plan_path.relative_to(ROOT)),
            "plan_sha256": sha256(plan_path),
            "assignments": str(assignments_path.relative_to(ROOT)),
            "assignments_sha256": sha256(assignments_path),
            "opponent_registry": str(registry_path.relative_to(ROOT)),
            "opponent_registry_sha256": sha256(registry_path),
            "conditions_dir": str(conditions_dir.relative_to(ROOT)),
            "condition_manifest_sha256": sha256(conditions_dir / "manifest.json"),
            "condition_spec_sha256": sha256(condition_spec),
            "evaluation_spec_sha256": sha256(evaluation_spec),
        },
        "runtime": {
            "target_model": plan["fixed_runtime"]["model"],
            "opponent_id": plan["opponent_O"]["id"],
            "opponent_model": plan["opponent_O"]["model"],
            "opponent_condition_independent": True,
            "opponent_behavior_hashes": opponent_hashes,
        },
        "stage0_qualification": {
            "rule_opponent_source": registry["source_file"],
            "rule_opponent_source_sha256": registry["source_file_sha256"],
            "report": registry["qualification_report"],
            "report_sha256": registry["qualification_report_sha256"],
            "raw_logs": registry["qualification_raw_logs"],
        },
        "backup_destination": plan["backup"]["destination"],
        "environment": {
            "python": sys.version.replace(os.linesep, " "),
            "platform": platform.platform(),
        },
        "dependencies": {
            "pyproject_sha256": sha256(ROOT / "pyproject.toml"),
            "uv_lock_sha256": sha256(ROOT / "uv.lock"),
            "source_patch_sha256": sha256(run_dir / "source.patch"),
        },
        "files": {str(path.relative_to(run_dir)): sha256(path) for path in tracked},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {run_dir}")
    print(f"commit={manifest['repository']['commit']} dirty={manifest['repository']['git_dirty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
