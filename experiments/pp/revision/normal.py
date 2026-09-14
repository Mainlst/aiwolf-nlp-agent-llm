"""B: randomized complete blocks, isolated games, bounded retries, blind status."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from experiments.pp.revision.common import (
    POLICIES,
    ROOT,
    TEAMS,
    before,
    child,
    digest,
    jsonl,
    load_credentials,
    lock,
    now,
    read,
    seconds_left,
    spec,
    verify,
    verify_imported,
    write,
)
from experiments.pp.revision.prepare import dump
from experiments.runtime.run_one import port_is_open, stop_process, wait_for_server


def call_failure(team, call):
    """Separate failed generation from a successful but poor model response."""
    if call.get("success") is not True or call.get("error_type"):
        return f"{team}:generation_failed:{call.get('error_type') or 'unsuccessful_call'}"
    return None


def provider_failures(logs, live=False):
    """Inspect completed log records; an active writer may have a partial last line."""
    reasons = []
    for path in Path(logs).glob("*/llm_calls.jsonl"):
        lines = path.read_text().splitlines(keepends=True)
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            if live and index == len(lines) - 1 and not line.endswith("\n"):
                continue
            try:
                call = json.loads(line)
                reason = call_failure(path.parent.name, call)
            except (json.JSONDecodeError, AttributeError):
                reason = f"{path.parent.name}:invalid_call_log"
            if reason:
                reasons.append(reason)
    reasons.extend(operational_failures(logs, live=live))
    return sorted(set(reasons))


def operational_failures(logs, live=False):
    """Reject action fallbacks and admitted requests missing a durable completion."""
    logs = Path(logs)
    reasons = []
    for path in logs.glob("*/request_audit/*.json"):
        record = read(path)
        team = path.parent.parent.name
        status = record.get("status")
        if status == "action_timeout":
            reasons.append(f"{team}:action_timeout")
        elif status == "pending" and not live:
            reasons.append(f"{team}:unfinished_request")
        elif status == "failed":
            reasons.append(f"{team}:generation_failed:{record.get('error_type')}")
        elif status not in ("pending", "complete"):
            reasons.append(f"{team}:invalid_request_audit")
    # Legacy results have no admission records. Apply the same timeout rule to
    # every old row, without inspecting its outcome.
    for path in logs.parent.glob("revision*.txt"):
        if "Action timed out:" in path.read_text():
            reasons.append(f"{path.stem}:action_timeout")
    return sorted(set(reasons))


def transient_failures(reasons):
    return bool(reasons) and all(
        r.endswith((":APIConnectionError", ":APITimeoutError", ":action_timeout", ":unfinished_request"))
        for r in reasons
    )


def critical_markers(run):
    return list((Path(run) / "attempts" / "_critical").glob("*.json"))


def signal_critical(run, folder, reasons):
    # Each worker owns a different file, so concurrent failures cannot race on
    # common.write's temporary path. These markers are included in frozen evidence.
    write(
        Path(run) / "attempts" / "_critical" / f"{folder.parent.name}-{folder.name}.json",
        {"detected_at": now().isoformat(), "reasons": reasons},
    )


def complete_game(folder):
    """Find a flushed final server result without assuming clients still run."""
    candidates = list((folder / "json").glob("*.json"))
    if len(candidates) == 1:
        try:
            game = read(candidates[0])
            finishes = {
                e["agent"] for e in game.get("entries", []) if json.loads(e["request"]).get("request") == "FINISH"
            }
            if game.get("win_side") in ("WEREWOLF", "VILLAGER") and len(finishes) == 5:
                return candidates[0]
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            pass  # the server may still be flushing this file
    return None


def audit_game(game, roster, logs, cell):
    """Audit every agent and the complete seat/role map without using the winner."""
    reasons = operational_failures(logs)
    expected = {(e["seat"], e["team"], e["role"]) for e in roster}
    observed = {(int(e["idx"]), e["team"], e["role"]) for e in game.get("agents", [])}
    if len(game.get("agents", [])) != 5 or observed != expected:
        reasons.append("full_roster_mismatch")
    gid = game.get("game_id")
    for index, team in enumerate(TEAMS):
        try:
            calls = jsonl(logs / team / "llm_calls.jsonl")
        except json.JSONDecodeError:
            reasons.append(f"{team}:invalid_call_log")
            continue
        if not calls:
            reasons.append(f"{team}:missing_calls")
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("model", {}), dict):
                reasons.append(f"{team}:invalid_call_log")
                continue
            model = call.get("model", {})
            if (
                model.get("model") != "gpt-5.6-luna"
                or str(model.get("temperature")) != "1.0"
                or str(model.get("effective_temperature")) != "1.0"
            ):
                reasons.append(f"{team}:configured_model_mismatch")
            if call.get("response_model_id") not in (None, "", "gpt-5.6-luna"):
                reasons.append(f"{team}:response_model_mismatch")
            if call.get("game_id") != gid:
                reasons.append(f"{team}:game_id_mismatch")
            expected_condition = POLICIES[cell[index + 1]] if index < 2 else ""
            if call.get("condition", "") != expected_condition:
                reasons.append(f"{team}:policy_mismatch")
            failure = call_failure(team, call)
            if failure:
                reasons.append(failure)
    return sorted(set(reasons))


def worker(run, block_id, cell, attempt):
    """One process owns five clients and a server. Called only by run()."""
    run = Path(run).resolve()
    manifest = verify(run)
    resilient = spec(run)["normal"].get("resilient", False) if (run / "spec.yml").exists() else False
    load_credentials()
    block = next(b for b in read(run / "schedule.json") if b["block_id"] == block_id)
    folder = run / "attempts" / f"{block_id}-{cell}" / str(attempt)
    lease = read(folder / "start.json")
    if block_id not in read(run / "state.json")["started"] or (run / "frozen.json").exists():
        raise ValueError("Worker has no active block lease")
    # The parent starts the timeout before launching/importing this worker.
    # Reserve the same 20 seconds for cleanup even after slow startup.
    elapsed = max(0.0, (now() - datetime.fromisoformat(lease["started_at"])).total_seconds())
    deadline = time.monotonic() + max(0.0, lease["timeout"] - elapsed - 20)
    if critical_markers(run) or time.monotonic() >= deadline:
        write(folder / "result.json", {"status": "missing", "reason": "critical_stop_or_deadline"})
        return
    server = yaml.safe_load((run / "servers" / f"{block_id}.yml").read_text())
    port = lease["port"]
    if port_is_open("127.0.0.1", port):
        write(folder / "result.json", {"status": "missing", "reason": "port_in_use"})
        return
    server["server"]["web_socket"]["port"] = port
    write(folder / "optimizer.json", read(run / "optimizers" / f"{block_id}.json"))
    server["matching"]["output_path"] = str(folder / "optimizer.json")
    for key, sub in (("json_logger", "json"), ("game_logger", "game"), ("realtime_broadcaster", "realtime")):
        if key in server:
            server[key]["output_dir"] = str(folder / sub)
    dump(folder / "server.yml", server)
    paths = []
    for team in TEAMS:
        config = yaml.safe_load((run / "cells" / cell / f"{team}.yml").read_text())
        config["web_socket"]["url"] = f"ws://127.0.0.1:{port}/ws"
        config["log"]["output_dir"] = str(folder / "logs" / team)
        if resilient:
            config.setdefault("experiment", {})["request_audit"] = True
            config["openai"].update(
                max_retries=1,
                connect_timeout_seconds=5,
                request_timeout_seconds=20,
            )
        path = folder / f"{team}.yml"
        dump(path, config)
        paths.append(path)
    write(folder / "runtime-hashes.json", {p.name: digest(p) for p in [folder / "server.yml", *paths]})
    processes, handles = [], []
    game_path = None
    error = None
    try:
        handle = (folder / "server.txt").open("w")
        handles.append(handle)
        server_process = subprocess.Popen(
            [manifest["server_binary"], "-c", str(folder / "server.yml")],
            cwd=manifest["server_cwd"],
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        processes.append(server_process)
        wait_for_server(server_process, "127.0.0.1", port, timeout=min(15, max(0, deadline - time.monotonic())))
        for team, path in zip(TEAMS, paths):
            if critical_markers(run) or time.monotonic() >= deadline:
                break
            handle = (folder / f"{team}.txt").open("w")
            handles.append(handle)
            processes.append(
                subprocess.Popen(
                    (
                        [sys.executable, "-m", "experiments.pp.revision.client", str(path)]
                        if resilient
                        else [sys.executable, "src/main.py", "-c", str(path)]
                    ),
                    cwd=ROOT,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
            )
        clients_finished_at = None
        while time.monotonic() < deadline:
            failures = provider_failures(folder / "logs", live=True)
            if failures:
                if not (resilient and transient_failures(failures)):
                    signal_critical(run, folder, failures)
                break
            game_path = complete_game(folder)
            if game_path is not None:
                break
            if critical_markers(run):
                error = "critical_stop"
                break
            if all(p.poll() is not None for p in processes[1:]):
                if clients_finished_at is None:
                    clients_finished_at = time.monotonic()
                if time.monotonic() - clients_finished_at >= 2:
                    break
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    except Exception as exc:
        error = type(exc).__name__
    finally:
        for process in reversed(processes):
            stop_process(process, timeout=2)
        for handle in handles:
            handle.close()
    # A last FINISH receiver can exit before the server publishes win_side;
    # graceful server shutdown can also flush that result after the polling loop.
    game_path = complete_game(folder)
    failures = provider_failures(folder / "logs")
    if game_path is None:
        result = {"status": "missing", "reason": error or "no_complete_game"}
        if failures:
            result = {"status": "critical", "reasons": failures}
    else:
        reasons = audit_game(read(game_path), block["roster"], folder / "logs", cell)
        result = {
            "status": "critical" if reasons else "complete",
            "reasons": reasons,
            "game_path": str(game_path.relative_to(run)),
            "game_sha256": digest(game_path),
        }
    if resilient and result["status"] == "critical" and transient_failures(result["reasons"]):
        result.update(status="retryable", terminal_game=False)
    if result["status"] == "critical":
        signal_critical(run, folder, result["reasons"])
    write(folder / "result.json", result)


def row_result(run, row_id):
    results = []
    for directory in sorted((Path(run) / "attempts" / row_id).glob("*")):
        if (directory / "result.json").exists():
            result = read(directory / "result.json")
            results.append({**result, "attempt": directory.name})
    # Never replace a completed game by a later attempt.
    return next(
        (r for r in results if r["status"] in ("complete", "critical") or r.get("terminal_game")),
        results[-1] if results else {"status": "missing", "reason": "not_finished"},
    )


def recover_attempt(run, block, cell, folder, cfg):
    """Recover interrupted terminal evidence before deciding to pay for a retry."""
    prior = read(folder / "result.json") if (folder / "result.json").exists() else None
    if prior and (prior["status"] in ("complete", "critical") or prior.get("terminal_game")):
        return prior
    game_path = complete_game(folder)
    failures = provider_failures(folder / "logs")
    if game_path is not None:
        reasons = audit_game(read(game_path), block["roster"], folder / "logs", cell)
        result = {
            "status": "critical" if reasons else "complete",
            "reasons": reasons,
            "game_path": str(game_path.relative_to(run)),
            "game_sha256": digest(game_path),
            "terminal_game": True,
            "recovered": True,
        }
        if reasons and cfg.get("resilient") and transient_failures(reasons):
            result.update(status="retryable", terminal_game=False)
        if not reasons:
            # File publication must fall inside both the original attempt lease
            # and the data window. A late/uncertain terminal game is never rerun.
            try:
                lease = read(folder / "start.json")
                started = datetime.fromisoformat(lease["started_at"])
                published = datetime.fromtimestamp(game_path.stat().st_mtime, tz=timezone.utc)
                cutoff = min(
                    datetime.fromisoformat(cfg["data_deadline"]),
                    started + timedelta(seconds=lease["timeout"]),
                )
                eligible = started <= published < cutoff
            except (FileNotFoundError, KeyError, ValueError, TypeError):
                eligible = False
            if not eligible:
                result.update(status="missing", reason="unverified_or_late_terminal_game")
    elif failures:
        status = "retryable" if cfg.get("resilient") and transient_failures(failures) else "critical"
        result = {"status": status, "reasons": failures, "recovered": True}
    else:
        return prior
    if result["status"] == "critical":
        signal_critical(run, folder, result["reasons"])
    write(folder / "result.json", result)
    return result


def available_memory_gib():
    fields = {line.split(":")[0]: line.split(":")[1].strip() for line in Path("/proc/meminfo").read_text().splitlines()}
    return int(fields["MemAvailable"].split()[0]) / 1048576


def run(run_dir, execute=False, repair_only=False):
    run = Path(run_dir).resolve()
    with lock(run):
        manifest = verify(run)
        verify_imported(run)
        if manifest["kind"] != "B":
            raise ValueError("Not a B run")
        cfg = spec(run)["normal"]
        schedule = read(run / "schedule.json")
        repair_rows = set(read(run / "lineage/continuation.json")["repair_rows"]) if repair_only else None
        if not execute:
            print(f"B dry-run: {len(schedule)} blocks / {len(schedule) * 4} games; no processes or API calls")
            return
        if (run / "frozen.json").exists():
            raise ValueError("Run is frozen")
        load_credentials()
        state = read(run / "state.json")
        # Recover already-paid evidence before any deadline/stop gate or new
        # process launch. Resuming after the cutoff must still recover games
        # published within their original leases, and surface earlier failures.
        for block in schedule:
            if block["block_id"] in state["started"]:
                for cell in block["conditions"]:
                    row_dir = run / "attempts" / f"{block['block_id']}-{cell}"
                    for folder in sorted(row_dir.glob("*")):
                        if folder.is_dir():
                            recover_attempt(run, block, cell, folder, cfg)
        if critical_markers(run):
            state["critical_stop"] = True
            write(run / "state.json", state)
        if state.get("critical_stop"):
            raise ValueError("Critical audit failure: prepare a new run after fixing the cause")
        stop = threading.Event()
        pause = threading.Event()

        def run_block(block, slot):
            for cell in block["conditions"]:
                row_id = f"{block['block_id']}-{cell}"
                if repair_rows is not None and row_id not in repair_rows:
                    continue
                prior = row_result(run, row_id)
                if prior["status"] == "critical":
                    stop.set()
                if prior["status"] in ("complete", "critical") or prior.get("terminal_game"):
                    continue
                for attempt in range(1, cfg["max_attempts"] + 1):
                    if critical_markers(run):
                        stop.set()
                    if stop.is_set() or not before(cfg["data_deadline"]):
                        return
                    if cfg.get("resilient") and available_memory_gib() < cfg["memory_reserve_gib"]:
                        pause.set()
                        return
                    folder = run / "attempts" / row_id / str(attempt)
                    if (folder / "start.json").exists():
                        continue  # interrupted calls count against the retry budget
                    timeout = min(cfg["game_timeout_seconds"], seconds_left(cfg["data_deadline"]))
                    write(
                        folder / "start.json",
                        {"started_at": now().isoformat(), "port": cfg["base_port"] + slot, "timeout": timeout},
                    )
                    command = [
                        sys.executable,
                        "-m",
                        "experiments.pp.revision.normal",
                        str(run),
                        "--worker",
                        block["block_id"],
                        cell,
                        str(attempt),
                        "--execute",
                    ]
                    code = child(command, folder / "worker.txt", timeout)
                    if not (folder / "result.json").exists():
                        recovered = recover_attempt(run, block, cell, folder, cfg)
                        if recovered is None:
                            write(folder / "result.json", {"status": "missing", "reason": f"worker_exit_{code}"})
                    result = read(folder / "result.json")
                    result["finished_at"] = now().isoformat()
                    result["elapsed_seconds"] = (
                        now() - datetime.fromisoformat(read(folder / "start.json")["started_at"])
                    ).total_seconds()
                    if result["status"] == "complete" and not before(cfg["data_deadline"]):
                        result.update(status="missing", reason="data_deadline", terminal_game=True)
                    write(folder / "result.json", result)
                    if result["status"] == "critical":
                        stop.set()
                    if result["status"] in ("complete", "critical") or result.get("terminal_game"):
                        break
                    if cfg.get("resilient") and attempt < cfg["max_attempts"]:
                        time.sleep(min(cfg["retry_backoff_seconds"], seconds_left(cfg["data_deadline"])))
            print(f"{block['block_id']}: operational processing finished (outcomes hidden)", flush=True)

        size = cfg["blocks_per_wave"]
        if size < 1 or not 1 <= cfg["max_parallel"] <= size:
            raise ValueError("Invalid wave/concurrency settings")
        for offset in range(0, len(schedule), size):
            verify(run)
            wave = schedule[offset : offset + size]
            if repair_rows is not None and not any(
                f"{b['block_id']}-{c}" in repair_rows for b in wave for c in b["conditions"]
            ):
                continue
            if cfg.get("resilient"):
                memory = available_memory_gib()
                with (run / "resources.jsonl").open("a") as stream:
                    stream.write(
                        json.dumps({"at": now().isoformat(), "available_memory_gib": memory, "wave_offset": offset})
                        + "\n"
                    )
                unfinished = sum(
                    any(row_result(run, f"{b['block_id']}-{c}")["status"] != "complete" for c in b["conditions"])
                    for b in wave
                )
                if memory < cfg["memory_reserve_gib"] + unfinished * cfg["memory_per_game_gib"]:
                    pause.set()
                    break
            ids = [b["block_id"] for b in wave]
            already_started = set(ids) <= set(state["started"])
            if not already_started:
                if not before(cfg["start_deadline"]) or stop.is_set():
                    break
                state["started"].extend(i for i in ids if i not in state["started"])
                write(run / "state.json", state)  # register the whole wave before launching any game
            if not before(cfg["data_deadline"]):
                break
            with ThreadPoolExecutor(max_workers=cfg["max_parallel"]) as pool:
                # Each slot is used by a single block in this wave.
                list(pool.map(lambda pair: run_block(*pair), [(b, i) for i, b in enumerate(wave)]))
            if stop.is_set() or pause.is_set():
                break
        if cfg.get("resilient"):
            state["memory_paused"] = pause.is_set()
            state["retry_exhausted_rows"] = [
                f"{b['block_id']}-{c}"
                for b in schedule
                for c in b["conditions"]
                if row_result(run, f"{b['block_id']}-{c}")["status"] != "complete"
                and len(list((run / "attempts" / f"{b['block_id']}-{c}").glob("*/start.json"))) >= cfg["max_attempts"]
            ]
        state["critical_stop"] = stop.is_set() or bool(critical_markers(run))
        state["schedule_finished"] = len(state["started"]) == len(schedule) and all(
            row_result(run, f"{b['block_id']}-{c}")["status"] == "complete" for b in schedule for c in b["conditions"]
        )
        write(run / "state.json", state)
        if state["critical_stop"]:
            raise RuntimeError(
                "Critical audit failure: new generation stopped; prepare a new run after fixing the cause"
            )
        if state.get("retry_exhausted_rows"):
            raise RuntimeError("Retry budget exhausted for rows: " + ", ".join(state["retry_exhausted_rows"]))
        if pause.is_set():
            raise RuntimeError("Paused for low available memory; run the same command after memory is available")
        print("B status saved; use freeze after completion or the data deadline. Outcomes remain hidden.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--execute", action="store_true", help="Actually launch games and make API calls")
    parser.add_argument(
        "--repair-only", action="store_true", help="Process only failed/interrupted rows in the continuation record"
    )
    parser.add_argument("--worker", nargs=3, metavar=("BLOCK", "CELL", "ATTEMPT"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.execute:
            raise ValueError("Worker requires --execute")
        worker(args.run_dir, *args.worker)
    else:
        run(args.run_dir, args.execute, repair_only=args.repair_only)


if __name__ == "__main__":
    main()
