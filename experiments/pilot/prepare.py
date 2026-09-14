#!/usr/bin/env python3
"""Resolve all pilot configs and create a provenance manifest without API calls."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "experiments" / "pilot"


def yaml_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def sha256(path: Path) -> str:
    """Return a file's SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    """Run a non-interactive git query."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write stable, human-readable YAML."""
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def resolved_agent_config(
    base: dict[str, Any],
    plan: dict[str, Any],
    condition: str,
    run_dir: Path,
) -> dict[str, Any]:
    """Apply exactly one preregistered condition to a deep copy of the base config."""
    config = yaml.safe_load(yaml.safe_dump(base))
    spec = plan["conditions"][condition]
    config["web_socket"]["url"] = plan["server_url"]
    config["web_socket"]["auto_reconnect"] = False
    config["agent"]["num"] = plan["target_agent_count"]
    config["agent"]["team"] = plan["target_team"]
    config["agent"]["guard"]["enabled"] = spec["output_guard_enabled"]
    config["agent"]["guard"]["disabled_behavior"] = spec["output_guard_disabled_behavior"]
    config["agent"]["guard"]["audit_all_outputs"] = True
    config["experiment"] = {
        "study_id": plan["study_id"],
        "condition": condition,
        "label": spec["label"],
        "dynamic_context_enabled": spec["dynamic_context_enabled"],
        "role_strategy_enabled": spec["role_strategy_enabled"],
        "generic_role_strategy": plan["generic_role_strategy"],
    }
    config["log"]["output_dir"] = str(run_dir / "agent-logs" / condition)
    config["log"]["console_output"] = True
    config["log"]["file_output"] = True
    return config


def resolved_server_config(
    base: dict[str, Any],
    plan: dict[str, Any],
    row: dict[str, str],
    run_dir: Path,
) -> dict[str, Any]:
    """Create an isolated one-game server config while sharing only optimizer state."""
    config = yaml.safe_load(yaml.safe_dump(base))
    matching = plan["server_matching"]
    config["server"]["web_socket"]["host"] = "127.0.0.1"
    config["server"]["web_socket"]["port"] = 8080
    config["matching"].update(
        {
            "self_match": matching["self_match"],
            "is_optimize": matching["is_optimize"],
            "team_count": matching["team_count"],
            "game_count": matching["game_count_per_row"],
            "infinite_loop": False,
            "output_path": str(run_dir / "server" / "match_optimizer.json"),
        }
    )
    row_log = run_dir / "server-logs" / row["row_id"]
    config["json_logger"].update({"enable": True, "output_dir": str(row_log / "json"), "filename": "{game_id}"})
    config["game_logger"].update({"enable": True, "output_dir": str(row_log / "game"), "filename": "{game_id}"})
    if "realtime_broadcaster" in config:
        config["realtime_broadcaster"].update(
            {"enable": True, "output_dir": str(row_log / "realtime"), "filename": "{game_id}"}
        )
    return config


def main() -> int:
    """Create a new immutable-by-convention run directory."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, help="Unique identifier, e.g. pilot-20260812")
    parser.add_argument("--server-config", type=Path, help="Override base English 5-player server YAML")
    parser.add_argument("--server-binary", type=Path, help="Server executable whose digest must be recorded")
    parser.add_argument("--require-qualified", action="store_true", help="Refuse unless all Stage 0 evidence exists")
    args = parser.parse_args()

    run_dir = ROOT / "experiments" / "runs" / args.run_id
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing run: {run_dir}")

    plan = yaml_mapping(PILOT / "plan.yml")
    opponent_pools = yaml_mapping(PILOT / "opponent-pools.yml")
    if args.require_qualified:
        missing = []
        for pool_id, pool in opponent_pools["pools"].items():
            qualification_path = ROOT / str(pool.get("qualification_log", ""))
            if not qualification_path.is_file():
                missing.append(pool_id)
                continue
            qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
            if not qualification.get("pools", {}).get(pool_id, {}).get("passed", False):
                missing.append(pool_id)
        if missing:
            raise FileNotFoundError(f"Missing Stage 0 qualification for pools: {', '.join(missing)}")
    agent_base_path = (ROOT / plan["base_agent_config"]).resolve()
    server_base_path = (args.server_config or (ROOT / plan["base_server_config"])).resolve()
    default_server_binary = ROOT.parent / "aiwolf-nlp-server-linux-amd64"
    server_binary = (args.server_binary or default_server_binary).resolve()
    if not server_base_path.is_file():
        raise FileNotFoundError(f"Server config not found: {server_base_path}")

    with (PILOT / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        assignments = list(csv.DictReader(handle))
    run_dir.mkdir(parents=True)
    for relative in (
        "agent-configs",
        "server-configs",
        "agent-logs",
        "server-logs",
        "events",
        "server",
        "protocol",
    ):
        (run_dir / relative).mkdir()

    shutil.copy2(PILOT / "plan.yml", run_dir / "plan.yml")
    shutil.copy2(PILOT / "assignments.csv", run_dir / "assignments.csv")
    shutil.copy2(PILOT / "opponent-pools.yml", run_dir / "opponent-pools.yml")
    shutil.copy2(PILOT / "opponent-qualification.json", run_dir / "opponent-qualification.json")
    shutil.copy2(agent_base_path, run_dir / "base-agent-config.yml")
    shutil.copy2(server_base_path, run_dir / "base-server-config.yml")
    for source_name in (
        "README.md",
        "CHECKLIST.md",
        "pilot/check.py",
        "pilot/prepare.py",
        "runtime/run_one.py",
        "evaluation/summarize.py",
        "runtime/opponents.py",
        "runtime/qualify_opponents.py",
        "runtime/freeze_artifacts.py",
        "evaluation/judge_inputs.py",
        "evaluation/judge_outputs.py",
        "evaluation/analyze.py",
        "evaluation/verify_saved_logs.py",
    ):
        shutil.copy2(ROOT / "experiments" / source_name, run_dir / "protocol" / Path(source_name).name)
    shutil.copytree(ROOT / "experiments" / "judge", run_dir / "protocol" / "judge")
    shutil.copytree(ROOT / "experiments" / "validation", run_dir / "protocol" / "validation")

    base_agent = yaml_mapping(agent_base_path)
    base_server = yaml_mapping(server_base_path)
    for condition in plan["conditions"]:
        dump_yaml(
            run_dir / "agent-configs" / f"{condition}.yml",
            resolved_agent_config(base_agent, plan, condition, run_dir),
        )
    for row in assignments:
        dump_yaml(
            run_dir / "server-configs" / f"{row['row_id']}.yml",
            resolved_server_config(base_server, plan, row, run_dir),
        )
    for pool_id in ("A", "B", "C"):
        stage0_row = {"row_id": f"stage0-{pool_id}"}
        stage0_config = resolved_server_config(base_server, plan, stage0_row, run_dir)
        stage0_config["matching"]["output_path"] = str(run_dir / "server" / f"stage0-{pool_id}-optimizer.json")
        dump_yaml(run_dir / "server-configs" / f"stage0-{pool_id}.yml", stage0_config)

    patch = git("diff", "--binary", "HEAD", check=False)
    (run_dir / "source.patch").write_text(patch + ("\n" if patch else ""), encoding="utf-8")
    (run_dir / "environment.txt").write_text(
        f"created_at={datetime.now(UTC).astimezone().isoformat()}\n"
        f"python={sys.version.replace(os.linesep, ' ')}\n"
        f"platform={platform.platform()}\n",
        encoding="utf-8",
    )

    tracked_files = sorted(path for path in run_dir.rglob("*") if path.is_file())
    server_repo = server_base_path.parents[1] if (server_base_path.parents[1] / ".git").exists() else None
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "study_id": plan["study_id"],
        "run_id": args.run_id,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "repository": {
            "root": str(ROOT),
            "commit": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "git_dirty": bool(git("status", "--porcelain")),
            "status": git("status", "--short"),
            "source_patch_sha256": sha256(run_dir / "source.patch"),
        },
        "server": {
            "base_config": str(server_base_path),
            "base_config_sha256": sha256(server_base_path),
            "binary": str(server_binary) if server_binary.is_file() else None,
            "binary_sha256": sha256(server_binary) if server_binary.is_file() else None,
            "repository_commit": git("rev-parse", "HEAD", cwd=server_repo) if server_repo else None,
            "repository_dirty": bool(git("status", "--porcelain", cwd=server_repo)) if server_repo else None,
        },
        "dependencies": {
            "uv_lock_sha256": sha256(ROOT / "uv.lock"),
            "pyproject_sha256": sha256(ROOT / "pyproject.toml"),
        },
        "secrets_recorded": False,
        "files": {},
    }
    for path in tracked_files:
        manifest["files"][str(path.relative_to(run_dir))] = sha256(path)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
