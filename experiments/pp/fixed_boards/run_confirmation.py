#!/usr/bin/env python3
"""Exercise the live TALK/VOTE generation path on 12 excluded scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import yaml
from ulid import ULID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from aiwolf_nlp_common.packet import Info, Request, Role, Status, Talk  # noqa: E402

from agent.possessed import Possessed  # noqa: E402
from agent.werewolf import Werewolf  # noqa: E402

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
AGENT_LOCKS = {
    name: threading.Lock()
    for name in (
        "Alice",
        "Benjamin",
        "Clara",
        "Daisuke",
        "Emma",
        "Felix",
        "George",
        "Hannah",
        "Isaac",
        "Julia",
        "Kenji",
        "Laura",
        "Minato",
        "Nora",
        "Oliver",
        "Priya",
        "Quinn",
        "Rin",
        "Shion",
        "Talia",
        "Uma",
        "Victor",
        "Wendy",
        "Xavier",
    )
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSON-object records."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def sha256(path: Path) -> str:
    """Hash a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    """Run a read-only Git query for provenance."""
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def close_agent_logging(agent: Possessed | Werewolf) -> None:
    """Close handlers so repeated character names cannot share files."""
    for handler in list(agent.agent_logger.logger.handlers):
        handler.close()
        agent.agent_logger.logger.removeHandler(handler)


def build_info(item: dict[str, Any], game_id: str) -> tuple[Info, list[Talk]]:
    """Convert one public snapshot to the same Info/Talk objects as the server client."""
    model_input = item["input"]
    perspective = model_input["perspective"]
    snapshot = model_input["snapshot"]
    history = [
        Talk(
            idx=int(public_event["sequence"]),
            day=int(public_event["day"]),
            turn=int(public_event["sequence"]),
            agent=str(public_event["actor"]),
            text=str(public_event["text"]),
        )
        for public_event in snapshot["public_history"]
        if public_event["type"] == "TALK"
    ]
    info = Info(
        game_id=game_id,
        day=int(snapshot["day"]),
        agent=str(perspective["agent"]),
        profile=None,
        medium_result=None,
        divine_result=None,
        executed_agent=snapshot.get("most_recently_executed"),
        attacked_agent=snapshot.get("most_recently_attacked"),
        vote_list=None,
        attack_vote_list=None,
        status_map={name: Status(value) for name, value in snapshot["status_map"].items()},
        role_map={str(perspective["agent"]): Role(str(perspective["role"]))},
        remain_count=4,
        remain_length=125,
        remain_skip=4,
    )
    return info, history


def run_unit(
    run_dir: Path,
    item: dict[str, Any],
    condition: str,
    conditions_dir: Path = ROOT / "experiments" / "pp" / "generated",
) -> dict[str, Any]:
    """Generate one TALK followed by one VOTE using the production Agent methods."""
    scenario_id = item["scenario_id"]
    role = str(item["input"]["perspective"]["role"])
    agent_name = str(item["input"]["perspective"]["agent"])
    game_id = str(ULID())
    config_path = conditions_dir / f"{condition}.yml"
    config = deepcopy(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    config["llm"]["sleep_time"] = 0
    config["log"]["console_output"] = False
    config["log"]["output_dir"] = str(run_dir / "agent-logs" / condition / scenario_id)
    config["agent"]["default_action_timeout_seconds"] = 120
    agent_class = Possessed if role == "POSSESSED" else Werewolf
    started = monotonic()
    started_at = datetime.now(UTC).astimezone().isoformat()
    with AGENT_LOCKS[agent_name]:
        agent = agent_class(config, agent_name, game_id, Role(role))
        info, history = build_info(item, game_id)
        agent.info = info
        agent.talk_history = history
        try:
            agent.initialize()
            agent.request = Request.TALK
            talk = agent.talk()
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
            talk = None
            vote = None
            error = {"type": type(exception).__name__, "message": str(exception)}
        finally:
            close_agent_logging(agent)
    return {
        "scenario_id": scenario_id,
        "input_sha256": item["input_sha256"],
        "condition": condition,
        "game_id": game_id,
        "configured_model": config["openai"]["model"],
        "configured_temperature": config["openai"]["temperature"],
        "talk": talk,
        "vote": vote,
        "success": error is None and isinstance(talk, str) and isinstance(vote, str),
        "error": error,
        "started_at": started_at,
        "elapsed_seconds": round(monotonic() - started, 6),
    }


def main() -> int:
    """Create a provenance-bound confirmation run and execute 24 paired units."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-parallel", type=int, default=6)
    parser.add_argument("--corpus-dir", type=Path, default=GENERATED)
    parser.add_argument("--conditions-dir", type=Path, default=ROOT / "experiments" / "pp" / "generated")
    args = parser.parse_args()
    if args.max_parallel < 1:
        raise ValueError("--max-parallel must be positive")
    if git("status", "--porcelain"):
        raise RuntimeError("Refusing to run confirmation from a dirty worktree")
    corpus_dir = args.corpus_dir.resolve()
    conditions_dir = args.conditions_dir.resolve()
    run_dir = ROOT / "experiments" / "runs" / args.run_id
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing run: {run_dir}")
    (run_dir / "agent-logs").mkdir(parents=True)
    inputs = read_jsonl(corpus_dir / "confirmation-inputs.jsonl")
    units = [(item, condition) for item in inputs for condition in ("P+", "P-PP")]
    manifest = {
        "schema_version": 1,
        "run_id": args.run_id,
        "purpose": "non-analysis generation/scoring route confirmation",
        "exclude_from_confirmatory_analysis": True,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "scenario_count": len(inputs),
        "unit_count": len(units),
        "planned_calls": len(units) * 2,
        "model": "gpt-5.6-luna",
        "temperature": 1.0,
        "corpus_dir": str(corpus_dir.relative_to(ROOT)),
        "conditions_dir": str(conditions_dir.relative_to(ROOT)),
        "repository": {
            "commit": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "dirty": False,
        },
        "files": {
            "confirmation-inputs.jsonl": sha256(corpus_dir / "confirmation-inputs.jsonl"),
            "confirmation-labels.jsonl": sha256(corpus_dir / "confirmation-labels.jsonl"),
            "corpus-manifest.json": sha256(corpus_dir / "manifest.json"),
            "P+.yml": sha256(conditions_dir / "P+.yml"),
            "P-PP.yml": sha256(conditions_dir / "P-PP.yml"),
            "runner.py": sha256(Path(__file__)),
            "src/agent/agent.py": sha256(ROOT / "src" / "agent" / "agent.py"),
            "src/agent/possessed.py": sha256(ROOT / "src" / "agent" / "possessed.py"),
            "src/agent/werewolf.py": sha256(ROOT / "src" / "agent" / "werewolf.py"),
            "src/utils/agent_logger.py": sha256(ROOT / "src" / "utils" / "agent_logger.py"),
        },
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(args.max_parallel, len(units))) as executor:
        futures = {
            executor.submit(run_unit, run_dir, item, condition, conditions_dir): (item["scenario_id"], condition)
            for item, condition in units
        }
        for future in as_completed(futures):
            record = future.result()
            results.append(record)
            print(f"{record['scenario_id']} {record['condition']}: success={record['success']}", flush=True)
    results.sort(key=lambda record: (record["scenario_id"], record["condition"]))
    (run_dir / "outputs.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in results),
        encoding="utf-8",
    )
    failures = [record for record in results if not record["success"]]
    print(f"Completed {len(results)} TALK+VOTE units; failures={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
