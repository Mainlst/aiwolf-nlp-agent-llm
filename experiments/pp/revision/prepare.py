"""Prepare frozen A/B schedules and policy assignments without starting processes."""

from __future__ import annotations

import argparse
import itertools
import random
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml

from experiments.pp.prepare_pilot import opponent_behavior_hashes, server_config
from experiments.pp.revision.common import (
    CELLS,
    HERE,
    POLICIES,
    ROLES,
    ROOT,
    TEAMS,
    canonical_hash,
    digest,
    jsonl,
    read,
    write,
)


def allocation(blocks, seed):
    """Balance ordered wolf/possessed seats; pair the entire five-player roster."""
    rng = random.Random(seed)
    placements = list(itertools.permutations(range(1, 6), 2))
    result = []
    while len(result) < blocks:
        rng.shuffle(placements)
        for wolf_seat, possessed_seat in placements:
            seats = [wolf_seat, possessed_seat]
            village = [i for i in range(1, 6) if i not in seats]
            rng.shuffle(village)
            roster = [dict(team=t, role=r, seat=s) for t, r, s in zip(TEAMS, ROLES, seats + village)]
            cells = list(CELLS)
            rng.shuffle(cells)
            result.append({"block_id": f"B{len(result) + 1:03}", "roster": roster, "conditions": cells})
            if len(result) == blocks:
                break
    rng.shuffle(result)
    return result


def optimizer(roster):
    roles = {role: [] for role in ("BODYGUARD", "MEDIUM", *ROLES)}
    for entry in roster:
        roles[entry["role"]].append(entry["seat"] - 1)
    return {
        "infinite_loop": False,
        "team_count": 5,
        "game_count": 1,
        "idx_team_map": {str(e["seat"] - 1): e["team"] for e in roster},
        "role_num_map": {r: len(v) for r, v in roles.items()},
        "ended_matches": [],
        "scheduled_matches": [{"role_idxs": roles, "weight": 1.0}],
    }


def policies():
    condition_dir = ROOT / "experiments/pp/v2/generated"
    for value in read(condition_dir / "manifest.json")["configs"].values():
        if digest(condition_dir / value["file"]) != value["sha256"]:
            raise ValueError("Existing P+/P-PP source differs from its manifest")
    configs = {
        p: yaml.safe_load((ROOT / "experiments/pp/v2/generated" / f"{p}.yml").read_text()) for p in POLICIES.values()
    }
    source = yaml.safe_load((ROOT / "config/config_5.yml").read_text())
    source["openai"].update(model="gpt-5.6-luna", temperature=1.0, max_retries=0)
    source["experiment"] = {
        "study_id": "aiwolfdial-2026-pp-revision",
        "opponent_id": "O",
        "condition_independent": True,
    }
    configs["O"] = source
    for config in configs.values():
        if config["llm"]["type"] != "openai" or config["openai"]["model"] != "gpt-5.6-luna":
            raise ValueError("Unexpected generator")
        if config["openai"]["temperature"] != 1.0:
            raise ValueError("Unexpected temperature")
        config["openai"]["max_retries"] = 0
        config["agent"]["num"] = 1
        config["web_socket"]["auto_reconnect"] = False
        config["log"].update(console_output=True, file_output=True)
    return configs


def cell_configs(configs, cell):
    """Only the two wolf-side policies vary between cells."""
    output = {}
    for index, team in enumerate(TEAMS):
        policy = POLICIES[cell[index + 1]] if index < 2 else "O"
        config = deepcopy(configs[policy])
        config["agent"]["team"] = team
        # Do not add a cell label to village configuration: exact equality matters.
        output[team] = config
    return output


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")


def validate_settings(settings):
    """Reject specifications the implementation cannot faithfully execute."""
    if settings["model"] != "gpt-5.6-luna" or settings["temperature"] != 1.0:
        raise ValueError("Revision model and temperature must match the original evaluation")
    normal, fixed = settings["normal"], settings["fixed"]
    binary_hash = normal.get("required_server_binary_sha256", "")
    if (
        not isinstance(binary_hash, str)
        or len(binary_hash) != 64
        or any(c not in "0123456789abcdef" for c in binary_hash)
    ):
        raise ValueError("Require a lowercase SHA-256 in normal.required_server_binary_sha256")
    if normal["primary"] != "G11-G00" or fixed["primary"] != "A11-A10":
        raise ValueError("Unsupported primary comparison")
    if fixed["repetitions"] != 2 or fixed["endpoint"] != "talk_vote_consistent_both_repetitions":
        raise ValueError("A requires the two-repetition consistency endpoint")
    if normal["max_attempts"] != (3 if normal.get("resilient") else 2) or fixed["max_attempts"] != 2:
        raise ValueError("Only one retry is permitted")
    if normal.get("resilient") and (
        not 1 <= normal["max_parallel"] <= 8
        or normal.get("memory_reserve_gib", 0) < 8
        or normal.get("memory_per_game_gib", 0) < 1
        or normal.get("retry_backoff_seconds", 0) < 5
    ):
        raise ValueError("Resilient B requires <=8 workers, memory headroom and retry backoff")
    if (
        normal["blocks"] < 1
        or not 1 <= normal["max_parallel"] <= normal["blocks_per_wave"]
        or fixed["max_parallel"] < 1
        or normal["game_timeout_seconds"] <= 20
        or fixed["group_timeout_seconds"] <= 0
    ):
        raise ValueError("Invalid size, concurrency, or timeout")
    if not 1 <= normal["base_port"] <= 65536 - normal["blocks_per_wave"]:
        raise ValueError("Wave ports exceed TCP range")
    start = datetime.fromisoformat(normal["start_deadline"])
    end = datetime.fromisoformat(normal["data_deadline"])
    a_end = datetime.fromisoformat(fixed["data_deadline"])
    if any(d.tzinfo is None for d in (start, end, a_end)) or start >= end or a_end != end:
        raise ValueError("Require timezone-aware deadlines, start before end, and shared A/B data deadline")
    analysis = settings["analysis"]
    if (
        analysis["bootstrap_iterations"] < 1
        or analysis["p_values"] != "not_reported"
        or analysis["interval"] != "percentile_paired_cluster_bootstrap_95"
    ):
        raise ValueError("Unsupported analysis specification")


def prepare(args):
    run = args.run_dir.resolve()
    if run.exists():
        raise FileExistsError(f"Refusing to overwrite {run}")
    settings = yaml.safe_load(args.spec.read_text())
    validate_settings(settings)
    configs = policies()
    corpus = ROOT / "experiments/pp/fixed_boards_v2/generated"
    for name, expected in read(corpus / "manifest.json")["files"].items():
        if digest(corpus / name) != expected:
            raise ValueError(f"Existing board corpus changed: {name}")
    server = args.server_config.resolve()
    binary = args.server_binary.resolve()
    if args.kind == "B" and (not server.is_file() or not binary.is_file()):
        raise ValueError("Server configuration/binary missing")
    if args.kind == "B" and digest(binary) != settings["normal"]["required_server_binary_sha256"]:
        raise ValueError("Server binary SHA-256 mismatch: use the original seatfixed binary before preparing B")
    if args.kind == "A" and args.purpose == "main" and not args.b_run:
        raise ValueError("Main A preparation requires --b-run (B must start before A execution)")
    if args.purpose == "smoke":
        settings["normal"]["blocks"] = 2
    run.mkdir(parents=True)
    dump(run / "spec.yml", settings)
    for policy, config in configs.items():
        dump(run / "policies" / f"{policy}.yml", config)
    manifest = {
        "kind": args.kind,
        "purpose": args.purpose,
        "run_id": run.name,
        "credential_file": "config/.env",
        "credential_copied": False,
        "b_run": str(args.b_run.resolve()) if args.b_run else None,
    }
    if args.kind == "B":
        blocks = allocation(settings["normal"]["blocks"], settings["seed"])
        write(run / "schedule.json", blocks)
        base = yaml.safe_load(server.read_text())
        if "duration" in base["game"]["talk"] or base["game"]["talk"]["max_count"]["per_agent"] != 4:
            raise ValueError("B requires the original turn-based, four-talk server rules")
        if base["game"]["agent_count"] != 5:
            raise ValueError("B requires five players")
        village_hashes = {}
        for cell in CELLS:
            for team, config in cell_configs(configs, cell).items():
                dump(run / "cells" / cell / f"{team}.yml", config)
                if team in TEAMS[2:]:
                    value = canonical_hash(config)
                    if team in village_hashes and value != village_hashes[team]:
                        raise ValueError("Village policy changed between cells")
                    village_hashes[team] = value
        for block in blocks:
            block_id = block["block_id"]
            dump(run / "servers" / f"{block_id}.yml", server_config(base, {"row_id": block_id}, run))
            write(run / "optimizers" / f"{block_id}.json", optimizer(block["roster"]))
        manifest.update(
            server_binary=str(binary),
            server_binary_sha256=digest(binary),
            server_cwd=str(server.parents[1]),
            village_config_hashes=village_hashes,
            village_behavior_hashes=opponent_behavior_hashes(configs["O"]),
        )
    else:
        prefix = "confirmation-" if args.purpose == "smoke" else ""
        for name in ("inputs.jsonl", "labels.jsonl"):
            shutil.copy2(corpus / (prefix + name), run / name)
        items = jsonl(run / "inputs.jsonl")
        rng = random.Random(settings["seed"])
        groups = []
        for item in items:
            for repetition in (1, 2):
                for talk_bit in ("0", "1"):
                    votes = ["0", "1"]
                    rng.shuffle(votes)
                    groups.append(
                        {
                            "group_id": f"{item['scenario_id']}_T{talk_bit}_R{repetition}",
                            "scenario_id": item["scenario_id"],
                            "talk_bit": talk_bit,
                            "repetition": repetition,
                            "vote_bits": votes,
                        }
                    )
        rng.shuffle(groups)
        write(run / "schedule.json", groups)
        for name in ("config.yml", "rubric.md"):
            dest = run / "judge-spec" / name
            dest.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT / "experiments/pp/fixed_boards/judge" / name, dest)
    # Exact source snapshots also include imported legacy helpers. Never copy .env.
    sources = sorted((ROOT / "src").rglob("*.py"))
    sources += sorted(
        p
        for p in (ROOT / "experiments").rglob("*.py")
        if "runs" not in p.relative_to(ROOT).parts and "__pycache__" not in p.parts
    )
    sources += [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    manifest["source_hashes"] = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    for source in sources:
        dest = run / "source" / source.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    manifest["files"] = {str(p.relative_to(run)): digest(p) for p in sorted(run.rglob("*")) if p.is_file()}
    write(run / "manifest.json", manifest)
    write(run / "state.json", {"started": [], "schedule_finished": False})
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("A", "B"))
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--purpose", choices=("main", "smoke"), default="main")
    parser.add_argument("--spec", type=Path, default=HERE / "spec.yml")
    parser.add_argument("--server-config", type=Path, default=ROOT / "config/server.example.yml")
    parser.add_argument("--server-binary", type=Path, default=ROOT / "server/aiwolf-nlp-server-linux-amd64-seatfixed")
    parser.add_argument("--b-run", type=Path)
    args = parser.parse_args()
    print(f"Prepared without API calls: {prepare(args)}")


if __name__ == "__main__":
    main()
