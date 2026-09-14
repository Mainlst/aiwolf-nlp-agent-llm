"""Offline validation of prepared revision runs; never initialize API clients."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import yaml

from experiments.pp.revision.common import CELLS, POLICIES, TEAMS, canonical_hash, freeze, jsonl, read, spec, verify
from experiments.pp.revision.prepare import cell_configs, optimizer, validate_settings


def check(run):
    manifest = verify(run)
    settings = spec(run)
    validate_settings(settings)
    schedule = read(run / "schedule.json")
    configs = {p: yaml.safe_load((run / "policies" / f"{p}.yml").read_text()) for p in ("P+", "P-PP", "O")}
    for policy, config in configs.items():
        if config["openai"]["model"] != settings["model"] or config["openai"]["temperature"] != settings["temperature"]:
            raise ValueError(f"Unexpected model setting: {policy}")
    if manifest["kind"] == "B":
        if len(schedule) != settings["normal"]["blocks"]:
            raise ValueError("B block count mismatch")
        ids = [b["block_id"] for b in schedule]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate block IDs")
        for b in schedule:
            if sorted(b["conditions"]) != sorted(CELLS):
                raise ValueError("Incomplete factorial block")
            if {e["seat"] for e in b["roster"]} != set(range(1, 6)):
                raise ValueError("Seat assignment is not a permutation")
            if read(run / "optimizers" / f"{b['block_id']}.json") != optimizer(b["roster"]):
                raise ValueError("Optimizer/roster mismatch")
        for cell in CELLS:
            expected = cell_configs(configs, cell)
            for team in TEAMS:
                value = yaml.safe_load((run / "cells" / cell / f"{team}.yml").read_text())
                if value != expected[team]:
                    raise ValueError("Wrong role policy allocation")
                if team in TEAMS[2:] and canonical_hash(value) != manifest["village_config_hashes"][team]:
                    raise ValueError("Village configuration changed")
        count = len(schedule) * 4
    else:
        inputs, labels = jsonl(run / "inputs.jsonl"), jsonl(run / "labels.jsonl")
        expected = 120 if manifest["purpose"] == "main" else 12
        if len(inputs) != expected or {i["scenario_id"] for i in inputs} != {i["scenario_id"] for i in labels}:
            raise ValueError("Wrong A corpus")
        keys = {(g["scenario_id"], g["talk_bit"], g["repetition"]) for g in schedule}
        expected_keys = {(i["scenario_id"], t, r) for i in inputs for t in POLICIES for r in (1, 2)}
        if keys != expected_keys or len(schedule) != len(keys):
            raise ValueError("A group schedule is incomplete/duplicated")
        if any(sorted(g["vote_bits"]) != ["0", "1"] for g in schedule):
            raise ValueError("A VOTE branch missing")
        count = len(schedule) * 3
    missing = [
        name
        for name in ("aiwolf_nlp_common", "langchain_openai", "dotenv", "openai", "ulid")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise ValueError(f"Missing dependencies: {missing}; use the project's virtual environment")
    return {
        "kind": manifest["kind"],
        "purpose": manifest["purpose"],
        "planned_games_or_generation_calls": count,
        "api_called": False,
        "credentials": "config/.env (read only at execution)",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--freeze", action="store_true", help="Freeze generated evidence for analysis (no API calls)")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    print(check(run))
    if args.freeze:
        freeze(run)
        print("Eligibility and evidence frozen; analysis may now read outcomes")


if __name__ == "__main__":
    main()
