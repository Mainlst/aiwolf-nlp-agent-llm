"""Analyze frozen revision data; never mix it with the published v2 samples."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from experiments.pp.fixed_boards.score_analysis import score_unit
from experiments.pp.fixed_boards.score_confirmation import named_target, role_co
from experiments.pp.revision.common import CELLS, digest, jsonl, lock, read, spec, verify_frozen, write
from experiments.pp.revision.fixed import all_units
from experiments.pp.revision.normal import row_result
from experiments.pp.v2.normal.analyze_results import final_role_map, proportion, quantile


def interval(clusters, iterations=10000, seed=20260907):
    """Resample paired clusters, retaining every row and its denominator."""
    if not clusters:
        return {"difference": None, "ci95": None, "clusters": 0}
    estimate = sum(sum(c) for c in clusters) / sum(len(c) for c in clusters)
    ci = None
    if len(clusters) >= 2:
        rng = random.Random(seed)
        values = []
        for _ in range(iterations):
            sample = rng.choices(clusters, k=len(clusters))
            values.append(sum(sum(c) for c in sample) / sum(len(c) for c in sample))
        ci = [quantile(values, 0.025), quantile(values, 0.975)]
    return {
        "difference": estimate,
        "ci95": ci,
        "clusters": len(clusters),
        "bootstrap_iterations": iterations,
        "seed": seed,
    }


def bounds(blocks):
    """Worst/best missing-outcome bounds among all registered blocks; not a CI."""
    if not blocks:
        return None
    low, high = 0, 0
    for row in blocks:
        plus, minus = row.get("G11"), row.get("G00")
        low += (0 if plus is None else int(plus)) - (1 if minus is None else int(minus))
        high += (1 if plus is None else int(plus)) - (0 if minus is None else int(minus))
    return [low / len(blocks), high / len(blocks)]


def live_diagnostics(game):
    """Descriptive first feasible PP only; explicit CO and first submitted votes."""
    roles = final_role_map(game)
    entries = [(e, json.loads(e["request"])) for e in game["entries"]]
    reached = None
    for _, request in entries:
        info = request.get("info") or {}
        alive = [a for a, status in (info.get("status_map") or {}).items() if status == "ALIVE"]
        alive_roles = [roles[a] for a in alive]
        if len(alive) == 3 and "WEREWOLF" in alive_roles and "POSSESSED" in alive_roles:
            reached = (info["day"], alive)
            break
    if reached is None:
        return {"reached": False}
    day, alive = reached
    wolf = next(a for a in alive if roles[a] == "WEREWOLF")
    possessed = next(a for a in alive if roles[a] == "POSSESSED")
    village = next(a for a in alive if a not in (wolf, possessed))
    talks = {wolf: [], possessed: []}
    votes, co = {}, {wolf: False, possessed: False}
    response_co = False
    for entry, request in entries:
        if (request.get("info") or {}).get("day") != day:
            continue
        agent = entry["agent"]
        if agent not in talks or agent in votes:
            continue
        text = str(entry.get("response") or "").strip()
        if request["request"] == "TALK":
            talks[agent].append(text)
            if role_co(text) == roles[agent]:
                co[agent] = True
                if agent == wolf and co[possessed]:
                    response_co = True
        if request["request"] == "VOTE":
            votes[agent] = text
    consistent = {}
    for agent in talks:
        declarations = [named_target(t, alive) for t in talks[agent]]
        declared = next((d for d in reversed(declarations) if d is not None), None)
        consistent[agent] = declared is not None and votes.get(agent) == declared
    return {
        "reached": True,
        "possessed_co": co[possessed],
        "wolf_co": co[wolf],
        "both_co": co[wolf] and co[possessed],
        "wolf_co_after_possessed_co": response_co,
        "wolf_consistent": consistent[wolf],
        "possessed_consistent": consistent[possessed],
        "both_vote_village": all(votes.get(a) == village for a in talks),
        "won_after_reach": game["win_side"] == "WEREWOLF",
    }


def analyze_b(run, frozen):
    state = frozen["state"]
    blocks = read(run / "schedule.json")
    started = [b for b in blocks if b["block_id"] in state["started"]]
    outcomes, records = [], []
    missing = defaultdict(lambda: defaultdict(int))
    for block in started:
        values = {}
        for cell in CELLS:
            row_id = f"{block['block_id']}-{cell}"
            result = row_result(run, row_id)
            if result["status"] != "complete":
                values[cell] = None
                missing[cell][result.get("reason", ";".join(result.get("reasons", [])) or "missing")] += 1
                continue
            path = run / result["game_path"]
            if digest(path) != result["game_sha256"]:
                raise ValueError("Game log hash mismatch")
            game = read(path)
            values[cell] = game["win_side"] == "WEREWOLF"
            records.append(
                {
                    "block_id": block["block_id"],
                    "condition": cell,
                    "game_id": game["game_id"],
                    "won": values[cell],
                    "diagnostics": live_diagnostics(game),
                }
            )
        outcomes.append(values)
    if len({r["game_id"] for r in records}) != len(records):
        raise ValueError("Duplicate game IDs")
    complete = [v for v in outcomes if all(v[c] is not None for c in CELLS)]
    cfg = spec(run)["analysis"]
    contrasts = {
        "G11-G00": (1, 0, 0, -1),
        "interaction": (1, -1, -1, 1),
        "G10-G00": (0, 1, 0, -1),
        "G01-G00": (0, 0, 1, -1),
    }
    effects = {
        name: interval(
            [[sum(w * int(v[c]) for w, c in zip(weights, ("G11", "G10", "G01", "G00")))] for v in complete],
            cfg["bootstrap_iterations"],
            cfg["seed"],
        )
        for name, weights in contrasts.items()
    }
    descriptive = {}
    for cell in CELLS:
        available = [r for r in records if r["condition"] == cell]
        reached = [r["diagnostics"] for r in available if r["diagnostics"]["reached"]]
        descriptive[cell] = {
            "available_games": len(available),
            "pp_reach": proportion(len(reached), len(available)),
            "after_reach": {
                key: proportion(sum(d[key] for d in reached), len(reached))
                for key in (
                    "possessed_co",
                    "wolf_co",
                    "both_co",
                    "wolf_co_after_possessed_co",
                    "wolf_consistent",
                    "possessed_consistent",
                    "both_vote_village",
                    "won_after_reach",
                )
            },
            "wolf_co_by_possessed_co": {
                str(co): proportion(
                    sum(d["wolf_co"] for d in reached if d["possessed_co"] == co),
                    sum(d["possessed_co"] == co for d in reached),
                )
                for co in (False, True)
            },
        }
    return {
        "kind": "B",
        "primary": effects.pop("G11-G00"),
        "auxiliary_contrasts": effects,
        "rates_complete_blocks": {c: proportion(sum(v[c] for v in complete), len(complete)) for c in CELLS},
        "started_blocks": len(started),
        "complete_blocks": len(complete),
        "incomplete_blocks": len(started) - len(complete),
        "unstarted_blocks": len(blocks) - len(started),
        "missing_reasons": dict(missing),
        "missing_outcome_bounds_not_ci": bounds(outcomes),
        "attempts": {
            c: sum(len(list((run / "attempts" / f"{b['block_id']}-{c}").glob("*/start.json"))) for b in started)
            for c in CELLS
        },
        "retries": {
            c: sum(
                max(0, len(list((run / "attempts" / f"{b['block_id']}-{c}").glob("*/start.json"))) - 1) for b in started
            )
            for c in CELLS
        },
        "descriptive_only": descriptive,
        "games": records,
        "caveat": "Complete-block selection may be informative; post-reach/CO strata are descriptive.",
    }


def talk_success(score):
    d, s = score["deterministic"], score["semantic"]
    return bool(
        s["pp_recognized"]
        and d["correct_co_or_response"]
        and score["talk_declared_ally_preserved"]
        and d["valid_target_selected"]
        and s["vote_coordination"]
    )


def analyze_a(run, frozen):
    units = all_units(run)
    items = {i["scenario_id"]: i for i in jsonl(run / "inputs.jsonl")}
    labels = {i["scenario_id"]: i for i in jsonl(run / "labels.jsonl")}
    semantic = {}
    for group in read(run / "schedule.json"):
        rows = [read(run / "judge" / group["group_id"] / f"R{r}.json") for r in (1, 2, 3)]
        if any(not r["success"] for r in rows):
            raise ValueError("All three valid judge repetitions are required")
        semantic[group["group_id"]] = {
            k: sum(r["result"][k]["label"] for r in rows) >= 2
            for k in ("pp_recognized", "ally_recognized", "vote_coordination")
        }
    grouped = defaultdict(list)
    for unit in units:
        if unit["input_sha256"] != items[unit["scenario_id"]]["input_sha256"]:
            raise ValueError("Unit input hash mismatch")
        score = score_unit(unit, items[unit["scenario_id"]], labels[unit["scenario_id"]], semantic[unit["group_id"]])
        declared = score["deterministic"]["declared_vote_target"]
        score["talk_declared_ally_preserved"] = declared is not None and declared != labels[unit["scenario_id"]].get(
            "correct_ally"
        )
        grouped[(unit["scenario_id"], unit["condition"])].append(score)
    boards = []
    for (scenario, cell), rows in sorted(grouped.items()):
        if len(rows) != 2 or {r["generation_repetition"] for r in rows} != {1, 2}:
            raise ValueError("Two paired repetitions are required")
        positive = labels[scenario]["established_pp"]
        boards.append(
            {
                "scenario_id": scenario,
                "condition": cell,
                "positive": positive,
                "role": labels[scenario]["perspective_role"],
                "stratum": labels[scenario]["stratum"],
                "cluster": rows[0]["content_cluster"],
                "consistent": all(r["components"]["talk_vote_consistent"] for r in rows),
                "composite": all(r["composite_pp_success_repetition"] for r in rows) if positive else None,
                "deterministic": all(
                    all(
                        r["deterministic"][k]
                        for k in (
                            "correct_co_or_response",
                            "ally_preserved",
                            "valid_target_selected",
                            "talk_vote_consistent",
                        )
                    )
                    for r in rows
                ),
                "village_vote": all(r["deterministic"]["valid_vote_target"] for r in rows),
                "false_pp": any(r["false_pp_repetition"] for r in rows) if not positive else None,
                "talk_success_vote_failure": any(
                    talk_success(r) and not r["components"]["talk_vote_consistent"] for r in rows
                ),
                "talk_failure_village_vote": any(
                    not talk_success(r) and r["deterministic"]["valid_vote_target"] for r in rows
                ),
                "components": {k: all(r["components"][k] for r in rows) for k in rows[0]["components"]},
            }
        )
    cells = ("A11", "A10", "A01", "A00")

    def summarize(subset):
        return {
            c: {
                k: proportion(
                    sum(bool(b[k]) for b in subset if b["condition"] == c), sum(b["condition"] == c for b in subset)
                )
                for k in (
                    "consistent",
                    "composite",
                    "deterministic",
                    "village_vote",
                    "talk_success_vote_failure",
                    "talk_failure_village_vote",
                )
            }
            for c in cells
        }

    positives = [b for b in boards if b["positive"]]
    controls = [b for b in boards if not b["positive"]]
    by_scenario = defaultdict(dict)
    for b in positives:
        by_scenario[b["scenario_id"]][b["condition"]] = b
    clusters = defaultdict(list)
    for values in by_scenario.values():
        clusters[values["A11"]["cluster"]].append(int(values["A11"]["consistent"]) - int(values["A10"]["consistent"]))
    cfg = spec(run)["analysis"]
    return {
        "kind": "A",
        "primary": interval(list(clusters.values()), cfg["bootstrap_iterations"], cfg["seed"]),
        "endpoint": "talk_vote_consistent_both_repetitions",
        "rates": summarize(positives),
        "by_role": {r: summarize([b for b in positives if b["role"] == r]) for r in ("POSSESSED", "WEREWOLF")},
        "component_rates": {
            c: {
                k: proportion(
                    sum(b["components"][k] for b in positives if b["condition"] == c),
                    sum(b["condition"] == c for b in positives),
                )
                for k in positives[0]["components"]
            }
            for c in cells
        },
        "false_pp_controls": {
            stratum: {
                c: proportion(
                    sum(b["false_pp"] for b in controls if b["condition"] == c and b["stratum"] == stratum),
                    sum(b["condition"] == c and b["stratum"] == stratum for b in controls),
                )
                for c in cells
            }
            for stratum in sorted({b["stratum"] for b in controls})
        },
        "boards": boards,
        "caveat": "Consistency includes agreement on an incorrect target; shared-base additional effect only.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    with lock(run):
        manifest, frozen = verify_frozen(run)
        output = run / "analysis"
        if output.exists():
            raise ValueError("Refusing to overwrite analysis")
        result = analyze_b(run, frozen) if manifest["kind"] == "B" else analyze_a(run, frozen)
        result.update(purpose=manifest["purpose"], run_id=manifest["run_id"], frozen_sha256=digest(run / "frozen.json"))
        write(output / "analysis.json", result)
        primary = result["primary"]
        rates = (
            result["rates_complete_blocks"]
            if manifest["kind"] == "B"
            else {c: v["consistent"] for c, v in result["rates"].items()}
        )
        lines = [
            f"# Revision {manifest['kind']} ({manifest['purpose']})",
            "",
            "| Condition | Successes | Total | Rate |",
            "| --- | ---: | ---: | ---: |",
        ]
        for c, v in rates.items():
            lines.append(f"| {c} | {v['numerator']} | {v['denominator']} | {v['proportion']} |")
        lines += [
            "",
            f"Primary difference: {primary['difference']}; paired cluster 95% CI: {primary['ci95']}.",
            "",
            result["caveat"],
            "",
            "Supplementary counts, missingness, and provenance: analysis.json",
            "",
        ]
        (output / "tables.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"Analysis written: {output}")


if __name__ == "__main__":
    main()
