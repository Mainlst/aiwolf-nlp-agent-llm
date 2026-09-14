#!/usr/bin/env python3
"""Launch the local server, opponent pool, and target agent for one pilot row."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import socket
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVER_REPO = ROOT.parent / "aiwolf-nlp-server"


def digest(path: Path) -> str:
    """Hash a file before execution."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def append_event(path: Path, event: dict[str, object]) -> None:
    """Append one JSONL event."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value


def validate_run_server_binary(plan: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Require format-comparison runs to use their plan-pinned server binary."""
    expected = plan.get("required_server_binary_sha256")
    if plan.get("formats") and not expected:
        raise ValueError("Format-comparison run is retired: no required server binary SHA-256")
    frozen = manifest.get("server", {}).get("binary_sha256")
    if expected and frozen != expected:
        raise ValueError(
            "Frozen server binary does not match the run plan: "
            f"expected={expected} manifest={frozen}",
        )


def server_address(config_path: Path) -> tuple[str, int]:
    """Read the generated server listen address."""
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    web_socket = config["server"]["web_socket"]
    host = str(web_socket["host"])
    port = int(web_socket["port"])
    return host, port


def dump_yaml(path: Path, value: dict[str, Any]) -> None:
    """Write a deterministic, human-readable runtime configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")


def materialize_runtime_configs(
    run_dir: Path,
    row: dict[str, str],
    port: int,
    attempt_id: str = "default",
) -> tuple[Path, Path, list[Path]]:
    """Derive row-isolated client/server configs without changing frozen inputs."""
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid TCP port: {port}")
    row_id = row["row_id"]
    runtime_dir = run_dir / "runtime-configs" / row_id / attempt_id
    server_source = run_dir / "server-configs" / f"{row_id}.yml"
    target_source = run_dir / "agent-configs" / f"{row['condition']}.yml"
    opponent_sources = sorted((run_dir / "opponent-configs").glob("*.yml"))
    if len(opponent_sources) != 4:
        raise ValueError(f"Expected four individual O configs, found {len(opponent_sources)}")

    server = yaml.safe_load(server_source.read_text(encoding="utf-8"))
    host = str(server["server"]["web_socket"]["host"])
    server["server"]["web_socket"]["port"] = port
    optimizer_seed = run_dir / "optimizer-seeds" / f"{row_id}.json"
    if not optimizer_seed.is_file():
        raise FileNotFoundError(f"Frozen optimizer seed is missing: {optimizer_seed}")
    runtime_optimizer = runtime_dir / "optimizer.json"
    runtime_optimizer.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(optimizer_seed, runtime_optimizer)
    server["matching"]["output_path"] = str(runtime_optimizer)
    server_log_dir = run_dir / "server-logs" / row_id / attempt_id
    if "json_logger" in server:
        server["json_logger"]["output_dir"] = str(server_log_dir / "json")
    if "game_logger" in server:
        server["game_logger"]["output_dir"] = str(server_log_dir / "game")
    if "realtime_broadcaster" in server:
        server["realtime_broadcaster"]["output_dir"] = str(server_log_dir / "realtime")
    server_path = runtime_dir / "server.yml"
    dump_yaml(server_path, server)

    websocket_url = f"ws://{host}:{port}/ws"
    target = yaml.safe_load(target_source.read_text(encoding="utf-8"))
    target["web_socket"]["url"] = websocket_url
    target["log"]["output_dir"] = str(run_dir / "agent-logs" / row["condition"] / row_id / attempt_id)
    target_path = runtime_dir / "target.yml"
    dump_yaml(target_path, target)

    opponent_paths: list[Path] = []
    for source in opponent_sources:
        opponent = yaml.safe_load(source.read_text(encoding="utf-8"))
        opponent["web_socket"]["url"] = websocket_url
        team = str(opponent["agent"]["team"])
        opponent["log"]["output_dir"] = str(
            run_dir / "agent-logs" / "opponents" / team / row_id / attempt_id,
        )
        path = runtime_dir / "opponents" / f"{team}.yml"
        dump_yaml(path, opponent)
        opponent_paths.append(path)
    return server_path, target_path, opponent_paths


def port_is_open(host: str, port: int) -> bool:
    """Return whether a TCP listener already occupies the experiment port."""
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def wait_for_server(process: subprocess.Popen[str], host: str, port: int, timeout: float = 15.0) -> None:
    """Wait until the launched server listens, failing if it exits early."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited before becoming ready: {process.returncode}")
        if port_is_open(host, port):
            return
        time.sleep(0.1)
    raise TimeoutError(f"Server did not listen on {host}:{port} within {timeout} seconds")


def stop_process(process: subprocess.Popen[str] | None, timeout: float = 10.0) -> int | None:
    """Terminate a child process and escalate only if it does not exit."""
    if process is None:
        return None
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
    return process.returncode


def find_game_result(
    run_dir: Path,
    row: dict[str, str],
    target_team: str,
    attempt_id: str,
) -> dict[str, object]:
    """Extract game identity, target placement, and roster from server JSON."""
    game_files = sorted((run_dir / "server-logs" / row["row_id"] / attempt_id / "json").glob("*.json"))
    if len(game_files) != 1:
        return {"game_log_count": len(game_files), "game_id": None, "actual_role": None}
    game = load_json(game_files[0])
    agents = [agent for agent in game.get("agents", []) if agent.get("team") == target_team]
    actual_role = agents[0].get("role") if len(agents) == 1 else None
    actual_seat = agents[0].get("idx") if len(agents) == 1 else None
    return {
        "game_log_count": 1,
        "game_id": game.get("game_id"),
        "actual_role": actual_role,
        "actual_seat": actual_seat,
        "roster": [
            {"idx": agent.get("idx"), "team": agent.get("team"), "role": agent.get("role")}
            for agent in game.get("agents", [])
        ],
        "win_side": game.get("win_side"),
        "server_json": str(game_files[0]),
        "server_json_sha256": digest(game_files[0]),
    }


def main() -> int:
    """Validate provenance, launch all five clients and server, then clean up."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("row_id")
    parser.add_argument("--confirm-pool", required=True, choices=("O",))
    parser.add_argument("--server-repo", type=Path, default=DEFAULT_SERVER_REPO)
    parser.add_argument("--server-binary", type=Path)
    parser.add_argument("--port", type=int, help="derive row-isolated runtime configs on this port")
    parser.add_argument("--game-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    server_repo = args.server_repo.resolve()
    if not (server_repo / "go.mod").is_file():
        raise FileNotFoundError(f"AIWolf server repository not found: {server_repo}")
    manifest = load_json(run_dir / "manifest.json")
    plan = yaml.safe_load((run_dir / "plan.yml").read_text(encoding="utf-8"))
    validate_run_server_binary(plan, manifest)
    manifest_binary = manifest.get("server", {}).get("binary")
    server_binary = (args.server_binary or Path(str(manifest_binary))).resolve()
    if not server_binary.is_file():
        raise FileNotFoundError(f"Server executable not found: {server_binary}")
    expected_binary_hash = manifest.get("server", {}).get("binary_sha256")
    if expected_binary_hash and digest(server_binary) != expected_binary_hash:
        raise ValueError("Server binary differs from the one frozen in manifest.json")

    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        matching = [row for row in csv.DictReader(handle) if row["row_id"] == args.row_id]
    if len(matching) != 1:
        raise ValueError(f"Unknown or duplicate row_id: {args.row_id}")
    row = matching[0]
    if args.confirm_pool != row["opponent_pool"]:
        raise ValueError(f"Pool mismatch: row requires {row['opponent_pool']}, confirmed {args.confirm_pool}")

    attempt_prefix = "dryrun-" if args.dry_run else "run-"
    attempt_id = (
        attempt_prefix
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    frozen_agent_config = run_dir / "agent-configs" / f"{row['condition']}.yml"
    frozen_server_config = run_dir / "server-configs" / f"{row['row_id']}.yml"
    frozen_opponent_configs = sorted((run_dir / "opponent-configs").glob("*.yml"))
    if args.port is None:
        agent_config = frozen_agent_config
        server_config = frozen_server_config
        opponent_configs = frozen_opponent_configs
    else:
        server_config, agent_config, opponent_configs = materialize_runtime_configs(
            run_dir,
            row,
            args.port,
            attempt_id,
        )
    events_dir = run_dir / "events" / row["row_id"] / attempt_id
    events_dir.mkdir(parents=True, exist_ok=False)
    server_output = events_dir / "server.txt"
    agent_output = events_dir / "agent.txt"
    server_command = [str(server_binary), "-c", str(server_config)]
    if len(opponent_configs) != 4:
        raise ValueError(f"Expected four individual O configs, found {len(opponent_configs)}")
    opponent_commands = [["uv", "run", "python3", "src/main.py", "-c", str(config)] for config in opponent_configs]
    agent_command = ["uv", "run", "python3", "src/main.py", "-c", str(agent_config)]
    commands = {"server": server_command, "opponents": opponent_commands, "agent": agent_command}
    event_path = run_dir / ("dry-run-execution.jsonl" if args.dry_run else "execution.jsonl")
    started = {
        "event": "dry_run" if args.dry_run else "start",
        "timestamp": datetime.now(UTC).astimezone().isoformat(),
        "attempt_id": attempt_id,
        "row": row,
        "server_repo": str(server_repo),
        "server_binary": str(server_binary),
        "server_binary_sha256": digest(server_binary),
        "agent_config": str(agent_config),
        "agent_config_sha256": digest(agent_config),
        "frozen_agent_config": str(frozen_agent_config),
        "frozen_agent_config_sha256": digest(frozen_agent_config),
        "server_config": str(server_config),
        "server_config_sha256": digest(server_config),
        "frozen_server_config": str(frozen_server_config),
        "frozen_server_config_sha256": digest(frozen_server_config),
        "optimizer_seed": str(run_dir / "optimizer-seeds" / f"{row['row_id']}.json"),
        "optimizer_seed_sha256": digest(run_dir / "optimizer-seeds" / f"{row['row_id']}.json"),
        "runtime_port": server_address(server_config)[1],
        "opponent_config_sha256": {config.stem: digest(config) for config in opponent_configs},
        "frozen_opponent_config_sha256": {config.stem: digest(config) for config in frozen_opponent_configs},
        "commands": commands,
    }
    append_event(event_path, started)
    print(f"server: {' '.join(server_command)}")
    for index, command in enumerate(opponent_commands, start=1):
        print(f"opponent-{index}: {' '.join(command)}")
    print(f"agent: {' '.join(agent_command)}")
    if args.dry_run:
        return 0

    host, port = server_address(server_config)
    if port_is_open(host, port):
        raise RuntimeError(f"Experiment port is already in use: {host}:{port}")

    server_process: subprocess.Popen[str] | None = None
    opponent_processes: list[subprocess.Popen[str]] = []
    agent_process: subprocess.Popen[str] | None = None
    handles: list[TextIO] = []
    timed_out = False
    try:
        server_handle = server_output.open("w", encoding="utf-8")
        agent_handle = agent_output.open("w", encoding="utf-8")
        handles.extend((server_handle, agent_handle))
        server_process = subprocess.Popen(
            server_command,
            cwd=server_repo,
            stdout=server_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_for_server(server_process, host, port)
        for config, command in zip(opponent_configs, opponent_commands, strict=True):
            opponent_handle = (events_dir / f"{config.stem}.txt").open("w", encoding="utf-8")
            handles.append(opponent_handle)
            opponent_processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=opponent_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                ),
            )
        agent_process = subprocess.Popen(
            agent_command,
            cwd=ROOT,
            stdout=agent_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            agent_returncode = agent_process.wait(timeout=args.game_timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            agent_returncode = stop_process(agent_process)
        try:
            opponent_returncodes = [process.wait(timeout=15.0) for process in opponent_processes]
        except subprocess.TimeoutExpired:
            opponent_returncodes = [stop_process(process) for process in opponent_processes]
    except BaseException as error:  # noqa: BLE001
        append_event(
            event_path,
            {
                "event": "error",
                "timestamp": datetime.now(UTC).astimezone().isoformat(),
                "attempt_id": attempt_id,
                "row_id": row["row_id"],
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        raise
    finally:
        agent_returncode = stop_process(agent_process)
        opponent_returncodes = [stop_process(process) for process in opponent_processes]
        server_returncode = stop_process(server_process)
        for handle in handles:
            handle.close()

    result = find_game_result(
        run_dir,
        row,
        str(yaml.safe_load(agent_config.read_text())["agent"]["team"]),
        attempt_id,
    )
    game_id = result.get("game_id")
    client_logs = [agent_output, *(events_dir / f"{config.stem}.txt" for config in opponent_configs)]
    finish_confirmed = [
        bool(game_id) and "Request.FINISH" in path.read_text(encoding="utf-8", errors="replace")
        for path in client_logs
    ]
    if timed_out and bool(game_id) and result.get("game_log_count") == 1 and all(finish_confirmed):
        timed_out = False
    append_event(
        event_path,
        {
            "event": "finish",
            "timestamp": datetime.now(UTC).astimezone().isoformat(),
            "attempt_id": attempt_id,
            "row_id": row["row_id"],
            "timed_out": timed_out,
            "agent_returncode": agent_returncode,
            "opponent_returncodes": opponent_returncodes,
            "client_finish_confirmed": finish_confirmed,
            "server_returncode_after_termination": server_returncode,
            **result,
        },
    )
    if timed_out:
        return 124
    placement_mismatch = (
        result.get("actual_role") != row["planned_role"]
        or str(result.get("actual_seat")) != row["target_seat"]
    )
    if (
        (agent_returncode != 0 and not finish_confirmed[0])
        or any(
            code != 0 and not confirmed
            for code, confirmed in zip(opponent_returncodes, finish_confirmed[1:], strict=True)
        )
        or result["game_log_count"] != 1
        or placement_mismatch
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
