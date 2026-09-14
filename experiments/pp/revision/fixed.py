"""A: persist one TALK and fork independent VOTEs; share TALK-only judgments."""

from __future__ import annotations

import argparse
import dataclasses
import enum
import fcntl
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml

from experiments.pp.revision.common import (
    POLICIES,
    ROOT,
    before,
    canonical_hash,
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
    write,
)

RESOURCES = {"config", "agent_logger", "llm_model", "talk_phase_event", "whisper_phase_event"}


@contextmanager
def admission_lock(run):
    """Order request admission and permanent stops across threads and workers."""
    with (run / ".request-lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def stop_records(run):
    return [read(p) for p in sorted((run / "groups/_stops").glob("*.json"))]


def record_stop(run, group, stage, reason):
    with admission_lock(run):
        path = run / "groups/_stops" / f"{group['group_id']}-{stage}.json"
        if not path.exists():
            write(
                path,
                {"group_id": group["group_id"], "stage": stage, "reason": reason, "recorded_at": now().isoformat()},
            )


def admit(run, path, metadata, deadline):
    """A saved start is a charged-attempt reservation, including interrupted calls."""
    with admission_lock(run):
        if stop_records(run) or not before(deadline):
            return False
        write(path, {**metadata, "started_at": now().isoformat()})
        return True


def persist_stops(run, state):
    records = stop_records(run)
    if records:
        state.update(permanent_stop=True, stop_reasons=records, schedule_finished=False)
        state["critical_stop"] = state.get("critical_stop", False) or any(
            r["reason"] != "attempts_exhausted" for r in records
        )
    write(run / "state.json", state)
    return bool(records)


def generation_call_critical(calls, stage, bit, game_id):
    return len(calls) != 1 or any(
        c.get("request", "").lower() != stage
        or c.get("game_id") != game_id
        or c.get("condition") != POLICIES[bit]
        or c.get("model", {}).get("model") != "gpt-5.6-luna"
        or str(c.get("model", {}).get("temperature")) != "1.0"
        or str(c.get("model", {}).get("effective_temperature")) != "1.0"
        or c.get("response_model_id") not in (None, "", "gpt-5.6-luna")
        or c.get("error_type") in {"AuthenticationError", "PermissionDeniedError", "NotFoundError"}
        for c in calls
    )


def encode(value):
    """Encode only known game data, never model clients, keys, or arbitrary objects."""
    if isinstance(value, enum.Enum):
        return {"enum": type(value).__name__, "value": value.value}
    if dataclasses.is_dataclass(value):
        return {
            "packet": type(value).__name__,
            "fields": {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)},
        }
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported state type: {type(value).__name__}")


def decode(value):
    from aiwolf_nlp_common import packet

    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if set(value) == {"enum", "value"}:
            if value["enum"] not in {"Role", "Status", "Request"}:
                raise ValueError("Unsupported enum")
            return getattr(packet, value["enum"])(value["value"])
        if set(value) == {"packet", "fields"}:
            if value["packet"] not in {"Info", "Talk", "Setting", "Judge", "Vote"}:
                raise ValueError("Unsupported packet")
            return getattr(packet, value["packet"])(**{k: decode(v) for k, v in value["fields"].items()})
        return {k: decode(v) for k, v in value.items()}
    return value


def snapshot(agent):
    return {
        "data": {k: encode(v) for k, v in vars(agent).items() if k not in RESOURCES},
        "events": {k: getattr(agent, k).is_set() for k in ("talk_phase_event", "whisper_phase_event")},
    }


def restore(agent, state):
    for k in list(vars(agent)):
        if k not in RESOURCES:
            delattr(agent, k)
    for k, v in state["data"].items():
        setattr(agent, k, decode(v))
    for k, value in state["events"].items():
        getattr(agent, k).set() if value else getattr(agent, k).clear()


def generate_stage(run, group, stage, bit, initial, game_id, recover_only=False):
    """Retry a failed request once from identical state; never regenerate a saved TALK."""
    sys.path.insert(0, str(ROOT / "src"))
    from aiwolf_nlp_common.packet import Request, Role
    from agent.possessed import Possessed
    from agent.werewolf import Werewolf
    from experiments.pp.fixed_boards.run_confirmation import build_info, close_agent_logging

    item = next(i for i in jsonl(run / "inputs.jsonl") if i["scenario_id"] == group["scenario_id"])
    group_dir = run / "groups" / group["group_id"]
    stage_name = "talk" if stage == "talk" else f"vote{bit}"
    result_path = group_dir / f"{stage_name}.json"
    cfg = spec(run)["fixed"]
    identity = {
        "group_sha256": canonical_hash(group),
        "input_sha256": canonical_hash(item),
        "state_before_sha256": canonical_hash(initial),
        "policy": POLICIES[bit],
        "policy_sha256": digest(run / "policies" / f"{POLICIES[bit]}.yml"),
        "game_id": game_id,
        "stage": stage,
    }
    request_hash = canonical_hash(identity)
    saved = None
    try:
        for attempt in range(1, cfg["max_attempts"] + 1):
            folder = group_dir / stage_name / str(attempt)
            start, result = folder / "start.json", folder / "result.json"
            if not start.exists():
                if result.exists():
                    raise ValueError("A result has no corresponding request start")
                continue
            if read(start).get("request_sha256") != request_hash:
                raise ValueError("A saved request provenance mismatch")
            if not result.exists():
                continue
            value = read(result)
            log_path = folder / "logs/llm_calls.jsonl"
            if (
                value.get("request_sha256") != request_hash
                or value.get("attempt") != attempt
                or value.get("state_before_sha256") != identity["state_before_sha256"]
                or value.get("policy") != POLICIES[bit]
                or value.get("game_id") != game_id
                or value.get("call_log") != str(log_path.relative_to(run))
                or value.get("status") not in {"complete", "critical", "missing"}
            ):
                raise ValueError("A saved result provenance mismatch")
            if value["status"] == "critical":
                record_stop(run, group, stage_name, "critical_generation_result")
                return value
            if value["status"] != "complete":
                continue
            calls = jsonl(log_path)
            if (
                generation_call_critical(calls, stage, bit, game_id)
                or calls[0].get("success") is not True
                or value.get("call_log_sha256") != digest(log_path)
                or value.get("state") is None
                or value.get("state_after_sha256") != canonical_hash(value["state"])
                or not isinstance(value.get("output"), str)
                or not before(cfg["data_deadline"], datetime.fromisoformat(value["completed_at"]))
            ):
                raise ValueError("A saved successful result failed its audit")
            if saved is not None:
                raise ValueError("A request has multiple saved successful attempts")
            saved = value
        if result_path.exists() and (saved is None or read(result_path) != saved):
            raise ValueError("A representative result differs from its saved attempt")
    except (ValueError, KeyError, TypeError, OSError):
        record_stop(run, group, stage_name, "generation_provenance_mismatch")
        raise
    if saved is not None:
        if not result_path.exists():
            write(result_path, saved)
        return saved
    if all((group_dir / stage_name / str(a) / "start.json").exists() for a in range(1, cfg["max_attempts"] + 1)):
        record_stop(run, group, stage_name, "attempts_exhausted")
        return None
    if recover_only:
        return None
    for attempt in range(1, cfg["max_attempts"] + 1):
        folder = group_dir / stage_name / str(attempt)
        if (folder / "start.json").exists():
            continue
        if not admit(
            run,
            folder / "start.json",
            {"input_state_sha256": canonical_hash(initial), "request_sha256": request_hash},
            cfg["data_deadline"],
        ):
            return None
        config = yaml.safe_load((run / "policies" / f"{POLICIES[bit]}.yml").read_text())
        config["llm"]["sleep_time"] = 0
        config["log"].update(console_output=False, file_output=True, output_dir=str(folder / "logs"))
        role = item["input"]["perspective"]["role"]
        cls = Possessed if role == "POSSESSED" else Werewolf
        agent = cls(config, item["input"]["perspective"]["agent"], game_id, Role(role))
        try:
            if initial is None:
                agent.info, agent.talk_history = build_info(item, game_id)
            else:
                restore(agent, initial)
                if snapshot(agent) != initial:
                    raise ValueError("State restoration mismatch")
            agent.initialize()
            agent.request = Request.TALK if stage == "talk" else Request.VOTE
            output = agent.talk() if stage == "talk" else agent.vote()
            calls = jsonl(folder / "logs/llm_calls.jsonl")
            critical = generation_call_critical(calls, stage, bit, game_id)
            completed = now()
            success = not critical and calls[0].get("success") is True and before(cfg["data_deadline"], completed)
            retryable = not critical and calls[0].get("error_type") in {
                "APIConnectionError",
                "APITimeoutError",
                "RateLimitError",
                "InternalServerError",
            }
            retryable = retryable or (not critical and not before(cfg["data_deadline"]))
            result = {
                "status": "complete" if success else "critical" if critical or not retryable else "missing",
                "output": output,
                "state": snapshot(agent) if success else None,
                "state_before_sha256": canonical_hash(initial),
                "policy": POLICIES[bit],
                "attempt": attempt,
                "game_id": game_id,
                "completed_at": completed.isoformat(),
                "call_log": str((folder / "logs/llm_calls.jsonl").relative_to(run)),
                "call_log_sha256": digest(folder / "logs/llm_calls.jsonl") if calls else None,
                "request_sha256": request_hash,
            }
            result["state_after_sha256"] = canonical_hash(result["state"])
        finally:
            close_agent_logging(agent)
        write(folder / "result.json", result)
        if result["status"] == "critical":
            record_stop(run, group, stage_name, "critical_generation_result")
        if result["status"] != "missing":
            write(result_path, result)
            return result
    record_stop(run, group, stage_name, "attempts_exhausted")
    return None


def group_worker(run, group_id, recover_only=False):
    run = Path(run).resolve()
    if not recover_only:
        verify(run)
        load_credentials()
    if group_id not in read(run / "state.json")["started"] or (run / "frozen.json").exists():
        raise ValueError("No active group lease")
    sys.path.insert(0, str(ROOT / "src"))
    from aiwolf_nlp_common.packet import Talk
    from ulid import ULID

    group = next(g for g in read(run / "schedule.json") if g["group_id"] == group_id)
    folder = run / "groups" / group_id
    identity = folder / "identity.json"
    if not identity.exists():
        if recover_only or stop_records(run):
            return
        write(identity, {"game_id": str(ULID())})
    game_id = read(identity)["game_id"]
    talk = generate_stage(run, group, "talk", group["talk_bit"], None, game_id, recover_only)
    if not talk or talk["status"] != "complete":
        return
    state = deepcopy(talk["state"])
    info = decode(state["data"]["info"])
    history = decode(state["data"]["talk_history"])
    history.append(
        Talk(
            idx=max((h.idx for h in history), default=0) + 1,
            day=info.day,
            turn=max((h.turn for h in history if h.day == info.day), default=0) + 1,
            agent=info.agent,
            text=talk["output"],
        )
    )
    state["data"]["talk_history"] = encode(history)
    write(folder / "branch-state.json", state)
    for bit in group["vote_bits"]:
        vote = generate_stage(run, group, "vote", bit, state, game_id, recover_only)
        if vote and vote["status"] == "critical":
            return
        if not vote:
            if stop_records(run):
                return
            continue
        condition = "A" + group["talk_bit"] + bit
        item = next(i for i in jsonl(run / "inputs.jsonl") if i["scenario_id"] == group["scenario_id"])
        write(
            run / "units" / f"{group_id}_{condition}.json",
            {
                "unit_id": f"{group_id}_{condition}",
                "group_id": group_id,
                "scenario_id": group["scenario_id"],
                "repetition": group["repetition"],
                "condition": condition,
                "input_sha256": item["input_sha256"],
                "game_id": game_id,
                "talk": talk["output"],
                "vote": vote["output"],
                "branch_state_sha256": canonical_hash(state),
                "talk_result_sha256": digest(folder / "talk.json"),
                "vote_result_sha256": digest(folder / f"vote{bit}.json"),
            },
        )


def all_units(run):
    schedule = read(run / "schedule.json")
    expected = {f"{g['group_id']}_A{g['talk_bit']}{b}" for g in schedule for b in ("0", "1")}
    paths = list((run / "units").glob("*.json"))
    if {p.stem for p in paths} != expected:
        raise ValueError("A requires every scheduled unit; no partial board selection")
    units = [read(p) for p in paths]
    expected_metadata = {
        f"{g['group_id']}_A{g['talk_bit']}{b}": (
            g["group_id"],
            g["scenario_id"],
            g["repetition"],
            f"A{g['talk_bit']}{b}",
        )
        for g in schedule
        for b in ("0", "1")
    }
    for path, unit in zip(paths, units):
        if (
            unit.get("unit_id") != path.stem
            or (unit.get("group_id"), unit.get("scenario_id"), unit.get("repetition"), unit.get("condition"))
            != expected_metadata[path.stem]
        ):
            raise ValueError("A unit metadata differs from the frozen schedule")
    for g in schedule:
        pair = [u for u in units if u["group_id"] == g["group_id"]]
        folder = run / "groups" / g["group_id"]
        state_hash = canonical_hash(read(folder / "branch-state.json"))
        for u in pair:
            vote_path = folder / f"vote{u['condition'][-1]}.json"
            vote = read(vote_path)
            talk = read(folder / "talk.json")
            if (
                u["branch_state_sha256"] != state_hash
                or vote["state_before_sha256"] != state_hash
                or u["talk_result_sha256"] != digest(folder / "talk.json")
                or u["vote_result_sha256"] != digest(vote_path)
                or u["talk"] != talk["output"]
                or u["vote"] != vote["output"]
                or talk["status"] != "complete"
                or vote["status"] != "complete"
            ):
                raise ValueError("A branch provenance mismatch")
    return units


def judge_fatal(result, config):
    return (result.get("error") or {}).get("type") in {
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "BadRequestError",
    } or result.get("response_model_id") not in (None, "", config["model"])


class JudgeBudget:
    """Bound concurrent estimated judge spend using the frozen judge configuration."""

    def __init__(self, run, config):
        from experiments.pp.fixed_boards.pp_judge import estimated_cost

        self.lock = threading.Lock()
        self.run = run
        self.stopped = False
        self.reserved = 0.0
        self.config = config
        self.spent = sum(
            estimated_cost(read(p).get("usage", {}), config) for p in (run / "judge").glob("*/R*-attempt*/result.json")
        )

    def reserve(self):
        with self.lock:
            if (
                self.stopped
                or stop_records(self.run)
                or self.spent + self.reserved + self.config["per_call_cost_limit_usd"]
                > self.config["batch_cost_limit_usd"]
            ):
                self.stopped = True
                return False
            self.reserved += self.config["per_call_cost_limit_usd"]
            return True

    def finish(self, result):
        from experiments.pp.fixed_boards.pp_judge import estimated_cost

        cost = estimated_cost(result.get("usage", {}), self.config)
        fatal = judge_fatal(result, self.config)
        with self.lock:
            self.reserved -= self.config["per_call_cost_limit_usd"]
            self.spent += cost
            self.stopped = self.stopped or fatal or cost > self.config["per_call_cost_limit_usd"]
        return fatal


def judge_group(run, group, client, budget, recover_only=False):
    """Judge each TALK three times, with actual VOTEs absent from the judge input."""
    from experiments.pp.fixed_boards.pp_judge import PPJudgeOutput, blind_unit, call_judge, evidence_errors

    folder = run / "judge" / group["group_id"]
    cfg = yaml.safe_load((run / "judge-spec/config.yml").read_text())
    deadline = spec(run)["fixed"]["data_deadline"]
    item = next(i for i in jsonl(run / "inputs.jsonl") if i["scenario_id"] == group["scenario_id"])
    label = next(i for i in jsonl(run / "labels.jsonl") if i["scenario_id"] == group["scenario_id"])
    unit = read(run / "units" / f"{group['group_id']}_A{group['talk_bit']}0.json")
    blind, private = blind_unit(unit, item, label, "revision-shared-talk-20260907")
    del blind["target_vote"]
    rubric = (run / "judge-spec/rubric.md").read_text() + (
        "\nFor this request-stage experiment, target_vote is intentionally absent. "
        "Classify only target_talk using public history and the scoring reference.\n"
    )
    request_hash = canonical_hash({"input": blind, "private": private, "config": cfg, "rubric": rubric, "group": group})
    for name, value in (("input.json", blind), ("private-map.json", private)):
        path = folder / name
        if path.exists() and read(path) != value:
            record_stop(run, group, "judge", "judge_input_mismatch")
            raise ValueError("Saved judge input differs from the shared TALK")
        if not path.exists():
            write(path, value)
    if (folder / "critical.json").exists():
        record_stop(run, group, "judge", "critical_judge_result")
        return
    for repetition in (1, 2, 3):
        path = folder / f"R{repetition}.json"
        stage = f"judge-R{repetition}"
        saved = None
        try:
            for attempt in (1, 2):
                attempt_dir = folder / f"R{repetition}-attempt{attempt}"
                start, result_path = attempt_dir / "start.json", attempt_dir / "result.json"
                if not start.exists():
                    if result_path.exists():
                        raise ValueError("Judge result has no corresponding request start")
                    continue
                if read(start).get("request_sha256") != request_hash:
                    raise ValueError("Saved judge request provenance mismatch")
                if not result_path.exists():
                    continue
                value = read(result_path)
                if (
                    value.get("request_sha256") != request_hash
                    or value.get("attempt") != attempt
                    or value.get("repetition") != repetition
                ):
                    raise ValueError("Saved judge result provenance mismatch")
                if judge_fatal(value, cfg):
                    record_stop(run, group, stage, "critical_judge_result")
                    return
                if not value["success"]:
                    continue
                PPJudgeOutput.model_validate(value["result"])
                if evidence_errors(value["result"], blind):
                    raise ValueError("Saved judge evidence does not match the TALK")
                if not before(deadline, datetime.fromisoformat(value["completed_at"])):
                    continue
                if saved is not None:
                    raise ValueError("Judge request has multiple saved successful attempts")
                saved = value
            if path.exists() and (saved is None or read(path) != saved):
                raise ValueError("Judge representative result differs from its saved attempt")
        except (ValueError, KeyError, TypeError, OSError):
            record_stop(run, group, stage, "judge_provenance_mismatch")
            raise
        if saved is not None:
            if not path.exists():
                write(path, saved)
            continue
        if all((folder / f"R{repetition}-attempt{a}" / "start.json").exists() for a in (1, 2)):
            record_stop(run, group, stage, "attempts_exhausted")
            return
        if recover_only:
            continue
        for attempt in (1, 2):
            attempt_dir = folder / f"R{repetition}-attempt{attempt}"
            if (attempt_dir / "start.json").exists():
                continue
            if not budget.reserve():
                return
            if not admit(run, attempt_dir / "start.json", {"request_sha256": request_hash}, deadline):
                with budget.lock:
                    budget.reserved -= cfg["per_call_cost_limit_usd"]
                return
            call_cfg = {**cfg, "timeout_seconds": min(cfg["timeout_seconds"], seconds_left(deadline))}
            result = call_judge(client, blind, call_cfg, rubric)
            result.update(
                completed_at=now().isoformat(), request_sha256=request_hash, attempt=attempt, repetition=repetition
            )
            write(attempt_dir / "result.json", result)
            if budget.finish(result):
                record_stop(run, group, stage, "critical_judge_result")
                write(folder / "critical.json", {"status": "critical", "group_id": group["group_id"]})
                return
            if result["success"] and before(deadline, datetime.fromisoformat(result["completed_at"])):
                write(path, result)
                break
        else:
            record_stop(run, group, stage, "attempts_exhausted")
            return


def run(run_dir, execute=False, judge=False):
    run = Path(run_dir).resolve()
    with lock(run):
        manifest = verify(run)
        if manifest["kind"] != "A":
            raise ValueError("Not an A run")
        schedule = read(run / "schedule.json")
        cfg = spec(run)["fixed"]
        if not execute:
            print(f"A dry-run: {len(schedule)} shared TALK groups, {len(schedule) * 3} generation calls; no API calls")
            return
        if (run / "frozen.json").exists():
            raise ValueError("Run is frozen")
        if manifest["purpose"] == "main":
            b_run = Path(manifest["b_run"])
            b_manifest = verify(b_run)
            if (
                b_manifest["kind"] != "B"
                or b_manifest["purpose"] != "main"
                or not read(b_run / "state.json")["started"]
            ):
                raise ValueError("Start the main B run before main A")
        state = read(run / "state.json")
        if persist_stops(run, state) or state.get("critical_stop") or state.get("permanent_stop"):
            raise ValueError("A has a permanent stop; prepare a new run after fixing the cause")
        # Recover every admitted group before any new paid work, even after the deadline.
        try:
            for g in schedule:
                if g["group_id"] in state["started"]:
                    group_worker(run, g["group_id"], recover_only=True)
                    if stop_records(run):
                        break
        finally:
            stopped = persist_stops(run, state)
        if stopped:
            raise ValueError("A has a permanent stop; no further requests were started")
        if judge:
            all_units(run)
            from openai import OpenAI
            import os

            try:
                for g in schedule:
                    judge_group(run, g, None, None, recover_only=True)
                    if stop_records(run):
                        break
            finally:
                stopped = persist_stops(run, state)
            if stopped:
                raise ValueError("A judge has a permanent stop; no further requests were started")
            load_credentials()
            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0)
            budget = JudgeBudget(run, yaml.safe_load((run / "judge-spec/config.yml").read_text()))
            try:
                with ThreadPoolExecutor(max_workers=cfg["max_parallel"]) as pool:
                    list(pool.map(lambda g: judge_group(run, g, client, budget), schedule))
                state["schedule_finished"] = all(
                    (run / "judge" / g["group_id"] / f"R{r}.json").exists() for g in schedule for r in (1, 2, 3)
                )
            finally:
                stopped = persist_stops(run, state)
            print(
                f"A judge complete={state['schedule_finished']}; estimated_spend_usd={budget.spent:.4f}; stopped={budget.stopped}"
            )
            if stopped:
                raise ValueError("A judge has a permanent stop; no further requests will be started")
            return
        load_credentials()
        size = cfg["max_parallel"]
        for offset in range(0, len(schedule), size):
            if not before(cfg["data_deadline"]):
                break
            wave = schedule[offset : offset + size]
            for g in wave:
                if g["group_id"] not in state["started"]:
                    state["started"].append(g["group_id"])
            write(run / "state.json", state)

            def launch(g):
                if stop_records(run):
                    return 0
                command = [
                    sys.executable,
                    "-m",
                    "experiments.pp.revision.fixed",
                    str(run),
                    "--worker",
                    g["group_id"],
                    "--execute",
                ]
                return child(
                    command,
                    run / "groups" / g["group_id"] / "worker.txt",
                    min(cfg["group_timeout_seconds"], seconds_left(cfg["data_deadline"])),
                )

            try:
                with ThreadPoolExecutor(max_workers=size) as pool:
                    codes = list(pool.map(launch, wave))
                for g, code in zip(wave, codes):
                    group_worker(run, g["group_id"], recover_only=True)
                    if code not in (0, 124):
                        # A process exit can follow a durable success checkpoint.
                        # Account admitted requests before classifying the worker exit.
                        folder = run / "groups" / g["group_id"]
                        interrupted = code in (130, 137, 143) or code < 0
                        complete = all(
                            (run / "units" / f"{g['group_id']}_A{g['talk_bit']}{bit}.json").exists()
                            for bit in ("0", "1")
                        )
                        if not interrupted and not complete:
                            record_stop(run, g, "worker", "worker_failure")
                        write(folder / "worker-exit.json", {"exit_code": code, "recorded_at": now().isoformat()})
            finally:
                stopped = persist_stops(run, state)
            print(
                f"A groups processed: {min(offset + size, len(schedule))}/{len(schedule)} (outcomes hidden)", flush=True
            )
            if stopped:
                break
        if persist_stops(run, state):
            raise ValueError("A generation has a permanent stop; no further requests will be started")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--judge", action="store_true", help="Run the shared TALK classifier after generation")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.execute:
            raise ValueError("Worker requires --execute")
        group_worker(args.run_dir, args.worker)
    else:
        run(args.run_dir, args.execute, args.judge)


if __name__ == "__main__":
    main()
