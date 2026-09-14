#!/usr/bin/env python3
"""Reproduce the confirmatory and secondary PP v2 normal-game analyses."""

# ruff: noqa: C901, D103, EM101, EM102, PLR0915, PLR2004, T201, TRY003

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WOLF_ROLES = {"WEREWOLF", "POSSESSED"}
VILLAGE_ROLES = {"VILLAGER", "SEER", "MEDIUM", "BODYGUARD"}
PP_ROLES = Counter({"WEREWOLF": 1, "POSSESSED": 1, "VILLAGER": 1})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def exact_mcnemar(plus_only: int, minus_only: int) -> float:
    discordant = plus_only + minus_only
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, value) for value in range(min(plus_only, minus_only) + 1))
    return min(1.0, 2 * tail / (2**discordant))


def paired_summary(
    pairs: list[tuple[bool, bool]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    table = Counter((int(plus), int(minus)) for plus, minus in pairs)
    differences = [int(plus) - int(minus) for plus, minus in pairs]
    rng = random.Random(seed)  # noqa: S311 - deterministic scientific bootstrap
    bootstrap = [
        sum(rng.choices(differences, k=len(differences))) / len(differences)
        for _ in range(iterations)
    ]
    plus_only = table[(1, 0)]
    minus_only = table[(0, 1)]
    return {
        "pairs": len(pairs),
        "paired_difference": sum(differences) / len(differences),
        "match_pair_bootstrap95": [quantile(bootstrap, 0.025), quantile(bootstrap, 0.975)],
        "bootstrap_iterations": iterations,
        "bootstrap_seed": seed,
        "paired_table": {
            "neither": table[(0, 0)],
            "minus_only": minus_only,
            "plus_only": plus_only,
            "both": table[(1, 1)],
        },
        "exact_mcnemar_p_value": exact_mcnemar(plus_only, minus_only),
    }


def holm(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, (total - index) * value)
        adjusted[name] = min(1.0, running)
    return adjusted


def proportion(successes: int, total: int) -> dict[str, Any]:
    return {
        "numerator": successes,
        "denominator": total,
        "proportion": successes / total if total else None,
        "wilson95": wilson(successes, total),
    }


def risk_ratio(plus_wins: int, plus_total: int, minus_wins: int, minus_total: int) -> dict[str, Any]:
    if not plus_wins or not minus_wins:
        return {"estimate": None, "wald95": None, "status": "zero_cell"}
    estimate = (plus_wins / plus_total) / (minus_wins / minus_total)
    standard_error = math.sqrt(1 / plus_wins - 1 / plus_total + 1 / minus_wins - 1 / minus_total)
    return {
        "estimate": estimate,
        "wald95": [
            math.exp(math.log(estimate) - 1.96 * standard_error),
            math.exp(math.log(estimate) + 1.96 * standard_error),
        ],
        "status": "finite",
    }


def conditional_odds_ratio(plus_only: int, minus_only: int) -> dict[str, Any]:
    if plus_only == 0 or minus_only == 0:
        return {"estimate": None, "wald95": None, "status": "zero_discordant_cell"}
    estimate = plus_only / minus_only
    standard_error = math.sqrt(1 / plus_only + 1 / minus_only)
    return {
        "estimate": estimate,
        "wald95": [
            math.exp(math.log(estimate) - 1.96 * standard_error),
            math.exp(math.log(estimate) + 1.96 * standard_error),
        ],
        "status": "finite",
    }


def final_role_map(game: dict[str, Any]) -> dict[str, str]:
    for entry in reversed(game["entries"]):
        request = json.loads(entry["request"])
        if request["request"] == "FINISH":
            return {str(name): str(role) for name, role in request["info"]["role_map"].items()}
    raise ValueError(f"game {game.get('game_id')} has no FINISH role map")


def declared_targets(text: str, survivors: list[str]) -> list[str]:
    output = []
    for name in survivors:
        pattern = rf"\bvote(?:\s+for|\s+on)?\s+{re.escape(name)}\b"
        if re.search(pattern, text, flags=re.IGNORECASE):
            output.append(name)
    return output


def live_pp_score(game: dict[str, Any], target_role: str) -> dict[str, Any]:
    """Score the first live PP decision with a deterministic post-hoc rubric."""
    roles = final_role_map(game)
    target = next(name for name, role in roles.items() if role == target_role)
    reached_day: int | None = None
    survivors: list[str] = []
    for entry in game["entries"]:
        request = json.loads(entry["request"])
        info = request.get("info") or {}
        status_map = info.get("status_map") or {}
        alive = [name for name, status in status_map.items() if status == "ALIVE"]
        if len(alive) != 3:
            continue
        live_roles = Counter(roles[name] for name in alive)
        if live_roles == PP_ROLES:
            reached_day = int(info["day"])
            survivors = alive
            break
    if reached_day is None:
        return {"game_pp_reached": False, "target_pp_reached": False}
    if target not in survivors:
        return {"game_pp_reached": True, "target_pp_reached": False, "reach_day": reached_day}

    ally = next(name for name in survivors if roles[name] in WOLF_ROLES and name != target)
    village = next(name for name in survivors if roles[name] in VILLAGE_ROLES)
    target_talks: list[str] = []
    submitted_vote: str | None = None
    ally_possessed_claim_seen = False
    werewolf_response = False
    for entry in game["entries"]:
        request = json.loads(entry["request"])
        info = request.get("info") or {}
        if info.get("day") != reached_day:
            continue
        response = str(entry.get("response", ""))
        if request["request"] == "TALK":
            if entry["agent"] == ally and re.search(r"\bpossessed\b|狂人", response, flags=re.IGNORECASE):
                ally_possessed_claim_seen = True
            if entry["agent"] == target:
                target_talks.append(response)
                if (
                    target_role == "WEREWOLF"
                    and ally_possessed_claim_seen
                    and re.search(r"\bwerewolf\b|人狼", response, flags=re.IGNORECASE)
                ):
                    werewolf_response = True
        elif request["request"] == "VOTE" and entry["agent"] == target:
            submitted_vote = response.strip()

    correct_co_or_response = (
        any(re.search(r"\bpossessed\b|狂人", talk, flags=re.IGNORECASE) for talk in target_talks)
        if target_role == "POSSESSED"
        else werewolf_response
    )
    pp_recognized = any(
        re.search(r"\b(?:PP|power[ -]?play|parity|possessed|werewolf)\b|狂人|人狼", talk, flags=re.IGNORECASE)
        for talk in target_talks
    )
    vote_coordination = any(
        ally.lower() in talk.lower()
        and village.lower() in talk.lower()
        and re.search(r"\b(?:vote|match|coordinate)", talk, flags=re.IGNORECASE)
        for talk in target_talks
    )
    declarations = [
        name
        for talk in target_talks
        for name in declared_targets(talk, survivors)
    ]
    ally_preserved = submitted_vote is not None and submitted_vote != ally
    valid_target_selected = submitted_vote == village
    talk_vote_consistent = bool(declarations) and declarations[-1] == submitted_vote
    components = {
        "pp_recognized": pp_recognized,
        "correct_co_or_response": correct_co_or_response,
        "ally_preserved": ally_preserved,
        "valid_target_selected": valid_target_selected,
        "vote_coordination": vote_coordination,
        "talk_vote_consistent": talk_vote_consistent,
    }
    return {
        "game_pp_reached": True,
        "target_pp_reached": True,
        "reach_day": reached_day,
        "target_name": target,
        "ally_name": ally,
        "village_name": village,
        "submitted_vote": submitted_vote,
        "components": components,
        "composite_pp_success": all(components.values()),
        "rubric_status": "post_hoc_deterministic_live_log_operationalization",
    }


def usage_tokens(path: Path, game_id: str) -> dict[str, int]:
    output = {"input": 0, "cached_input": 0, "output": 0}
    for record in jsonl(path):
        if record.get("game_id") != game_id:
            continue
        usage = record.get("usage", {})
        output["input"] += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
        output["cached_input"] += int(
            usage.get("input_token_details.cache_read", usage.get("prompt_tokens_details.cached_tokens", 0)),
        )
        output["output"] += int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    return output


def group(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[tuple(str(record[key]) for key in keys)].append(record)
    output = []
    for values, rows in sorted(groups.items()):
        wins = sum(bool(row["won"]) for row in rows)
        output.append({**dict(zip(keys, values, strict=True)), **proportion(wins, len(rows))})
    return output


def paired_values(records: list[dict[str, Any]], field: str) -> list[tuple[bool, bool]]:
    pairs: dict[str, dict[str, bool]] = defaultdict(dict)
    for record in records:
        pairs[record["match_pair_id"]][record["condition"]] = bool(record[field])
    if any(set(value) != {"P+", "P-PP"} for value in pairs.values()):
        raise ValueError(f"incomplete pairs for {field}")
    return [(value["P+"], value["P-PP"]) for _, value in sorted(pairs.items())]


def verify_matching(records: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ("planned_role", "target_seat", "opponent_pool", "analysis_block", "optimizer_seed")
    pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        pairs[record["match_pair_id"]].append(record)
    failures = []
    for pair_id, rows in pairs.items():
        if len(rows) != 2 or {row["condition"] for row in rows} != {"P+", "P-PP"}:
            failures.append(f"{pair_id}: incomplete")
        failures.extend(
            f"{pair_id}: {field}"
            for field in fields
            if len({row[field] for row in rows}) != 1
        )
    if failures:
        raise ValueError(f"matched design failures: {failures[:5]}")
    return {
        "matched_pairs": len(pairs),
        "exactly_matched_fields": list(fields),
        "opponent_pool_levels": sorted({record["opponent_pool"] for record in records}),
        "interpretation": (
            "pair-stratified analysis conditions on role, pool, seat, block, and optimizer seed by design"
        ),
    }


def write_games(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "row_id",
        "match_pair_id",
        "condition",
        "planned_role",
        "target_seat",
        "analysis_block",
        "game_id",
        "won",
        "win_side",
        "game_pp_reached",
        "target_pp_reached",
        "composite_pp_success",
        "target_input_tokens",
        "target_output_tokens",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in fields})


def percent(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def write_table(path: Path, result: dict[str, Any]) -> None:
    conditions = {row["condition"]: row for row in result["primary"]["condition_summary"]}
    paired = result["primary"]["paired_comparison"]
    reach = {row["condition"]: row for row in result["secondary"]["pp_reach"]}
    post = {row["condition"]: row for row in result["secondary"]["post_reach"]}
    lines = [
        "# PP v2 normal-game result tables",
        "",
        "## Primary werewolf-team win rate",
        "",
        "| Condition | Wins / games | Win rate | Wilson 95% CI |",
        "| --- | ---: | ---: | ---: |",
    ]
    for condition in ("P+", "P-PP"):
        item = conditions[condition]
        lines.append(
            f"| {condition} | {item['numerator']} / {item['denominator']} | {percent(item['proportion'])} | "
            f"{percent(item['wilson95'][0])} to {percent(item['wilson95'][1])} |",
        )
    lines += [
        "",
        f"Paired difference: {percent(paired['paired_difference'])}; match-pair bootstrap 95% CI "
        f"{percent(paired['match_pair_bootstrap95'][0])} to {percent(paired['match_pair_bootstrap95'][1])}; "
        f"two-sided exact McNemar p={paired['exact_mcnemar_p_value']:.6f}.",
        "",
        "## Secondary PP reach and post-reach outcomes",
        "",
        "| Condition | PP reach | Post-reach composite success | Post-reach win |",
        "| --- | ---: | ---: | ---: |",
    ]
    for condition in ("P+", "P-PP"):
        lines.append(
            f"| {condition} | {reach[condition]['numerator']}/{reach[condition]['denominator']} "
            f"({percent(reach[condition]['proportion'])}) | "
            f"{post[condition]['composite_pp_success']['numerator']}/"
            f"{post[condition]['composite_pp_success']['denominator']} "
            f"({percent(post[condition]['composite_pp_success']['proportion'])}) | "
            f"{post[condition]['win']['numerator']}/{post[condition]['win']['denominator']} "
            f"({percent(post[condition]['win']['proportion'])}) |",
        )
    lines += [
        "",
        "Post-reach results are selected secondary outcomes. Composite success uses a post-hoc deterministic live-log "
        "operationalization and is not the blinded fixed-board judge endpoint.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_svg(path: Path, result: dict[str, Any]) -> None:
    fixed = result["multiplicity"]["fixed_board_effect"]
    normal = result["primary"]["paired_comparison"]
    rows = [
        ("Fixed-board PP success", fixed["difference"], fixed["ci95"]),
        ("Normal-game win rate", normal["paired_difference"], normal["match_pair_bootstrap95"]),
    ]
    left, right = -0.15, 0.95
    x0, width = 220, 600
    def scale(value: float) -> float:
        return x0 + (value - left) / (right - left) * width
    body = []
    for index, (label, estimate, interval) in enumerate(rows):
        y = 75 + index * 70
        body.append(f'<text x="15" y="{y + 5}" font-size="15">{label}</text>')
        body.append(
            f'<line x1="{scale(interval[0]):.1f}" y1="{y}" x2="{scale(interval[1]):.1f}" y2="{y}" '
            'stroke="#315f8c" stroke-width="5"/>',
        )
        body.append(f'<circle cx="{scale(estimate):.1f}" cy="{y}" r="7" fill="#d95f02"/>')
        body.append(f'<text x="835" y="{y + 5}" font-size="14">{100 * estimate:+.1f} pp</text>')
    ticks = []
    for value in (-0.1, 0.0, 0.2, 0.4, 0.6, 0.8):
        x = scale(value)
        ticks.append(f'<line x1="{x:.1f}" y1="45" x2="{x:.1f}" y2="165" stroke="#ddd"/>')
        ticks.append(f'<text x="{x - 15:.1f}" y="190" font-size="12">{100 * value:.0f}</text>')
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="930" height="210" viewBox="0 0 930 210">'
        '<rect width="100%" height="100%" fill="white"/>'
        + "".join(ticks)
        + f'<line x1="{scale(0):.1f}" y1="40" x2="{scale(0):.1f}" y2="170" stroke="#333" stroke-dasharray="4 4"/>'
        + "".join(body)
        + '<text x="480" y="207" text-anchor="middle" font-size="13">P+ minus P-PP (percentage points)</text>'
        + "</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def saved_game_path(run_dir: Path, value: str) -> Path:
    """Resolve a distributed game log without consulting the original machine."""
    path = (run_dir / value).resolve()
    if not path.is_relative_to(run_dir.resolve()):
        raise ValueError("Game log must be inside the supplied run directory")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--fixed-analysis",
        type=Path,
        required=True,
    )
    parser.add_argument("--bootstrap-iterations", type=int, default=100_000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260815)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir.resolve()
    fixed_path = args.fixed_analysis.resolve()
    eligibility_path = run_dir / "eligibility-frozen.json"
    eligibility = json.loads(eligibility_path.read_text(encoding="utf-8"))
    if eligibility["eligible_games"] != 400 or eligibility["excluded_games"] != 0:
        raise ValueError("analysis requires the frozen 400/400 eligible set")
    with (run_dir / "assignments.csv").open(encoding="utf-8", newline="") as handle:
        assignments = {row["row_id"]: row for row in csv.DictReader(handle)}
    finishes = {
        record["row_id"]: record
        for record in jsonl(run_dir / "execution.jsonl")
        if record.get("event") == "finish"
    }
    records = []
    for row_id, decision in sorted(eligibility["decisions"].items()):
        if not decision["eligible"]:
            continue
        row = assignments[row_id]
        finish = finishes[row_id]
        game = json.loads(saved_game_path(run_dir, finish["server_json"]).read_text(encoding="utf-8"))
        actual_role = str(finish["actual_role"])
        won = actual_role in WOLF_ROLES and str(finish["win_side"]).upper() == "WEREWOLF"
        pp = live_pp_score(game, actual_role)
        attempt_id = str(finish["attempt_id"])
        usage_path = run_dir / "agent-logs" / row["condition"] / row_id / attempt_id / "llm_usage.jsonl"
        tokens = usage_tokens(usage_path, str(finish["game_id"]))
        records.append(
            {
                **row,
                "game_id": finish["game_id"],
                "actual_role": actual_role,
                "win_side": finish["win_side"],
                "won": won,
                **pp,
                "target_input_tokens": tokens["input"],
                "target_cached_input_tokens": tokens["cached_input"],
                "target_output_tokens": tokens["output"],
            },
        )
    if len(records) != 400 or len({record["game_id"] for record in records}) != 400:
        raise ValueError("eligible record reconstruction failed")

    matching = verify_matching(records)
    win_pairs = paired_values(records, "won")
    paired = paired_summary(
        win_pairs,
        iterations=args.bootstrap_iterations,
        seed=args.bootstrap_seed,
    )
    condition_summary = group(records, ("condition",))
    by_condition = {item["condition"]: item for item in condition_summary}
    paired_table = paired["paired_table"]
    fixed = json.loads(fixed_path.read_text(encoding="utf-8"))
    fixed_p = float(fixed["primary_comparison"]["paired_cluster_permutation_p_value"])
    adjusted = holm(
        {
            "fixed_board_composite_pp_success": fixed_p,
            "normal_game_werewolf_team_win_rate": paired["exact_mcnemar_p_value"],
        },
    )

    reach_rows = []
    post_rows: list[dict[str, Any]] = []
    for condition in ("P+", "P-PP"):
        condition_records = [record for record in records if record["condition"] == condition]
        reached = [record for record in condition_records if record["target_pp_reached"]]
        reach_rows.append({"condition": condition, **proportion(len(reached), len(condition_records))})
        components = {
            component: proportion(sum(bool(record["components"][component]) for record in reached), len(reached))
            for component in (
                "pp_recognized",
                "correct_co_or_response",
                "ally_preserved",
                "valid_target_selected",
                "vote_coordination",
                "talk_vote_consistent",
            )
        }
        post_rows.append(
            {
                "condition": condition,
                "reached_games": len(reached),
                "composite_pp_success": proportion(
                    sum(bool(record["composite_pp_success"]) for record in reached),
                    len(reached),
                ),
                "win": proportion(sum(bool(record["won"]) for record in reached), len(reached)),
                "components": components,
            },
        )

    target_tokens = []
    for condition in ("P+", "P-PP"):
        condition_records = [record for record in records if record["condition"] == condition]
        target_tokens.append(
            {
                "condition": condition,
                "games": len(condition_records),
                "input_tokens": sum(int(record["target_input_tokens"]) for record in condition_records),
                "cached_input_tokens": sum(int(record["target_cached_input_tokens"]) for record in condition_records),
                "output_tokens": sum(int(record["target_output_tokens"]) for record in condition_records),
            },
        )
    token_pairs: dict[str, dict[str, int]] = defaultdict(dict)
    for record in records:
        token_pairs[record["match_pair_id"]][record["condition"]] = int(record["target_input_tokens"])
    input_differences = [value["P+"] - value["P-PP"] for value in token_pairs.values()]

    role_pairs = {}
    for role in ("POSSESSED", "WEREWOLF"):
        subset = [record for record in records if record["actual_role"] == role]
        role_pairs[role] = paired_summary(
            paired_values(subset, "won"),
            iterations=args.bootstrap_iterations,
            seed=args.bootstrap_seed + (1 if role == "POSSESSED" else 2),
        )

    result = {
        "schema_version": 1,
        "run_id": eligibility["run_id"],
        "analysis_scope": "gpt-5.6-luna self-play with one target agent switched between P+ and P-PP",
        "provenance": {
            "eligibility_frozen_sha256": sha256(eligibility_path),
            "assignments_sha256": sha256(run_dir / "assignments.csv"),
            "execution_sha256": sha256(run_dir / "execution.jsonl"),
            "fixed_analysis_sha256": sha256(fixed_path),
            "eligible_games": len(records),
            "excluded_games": 0,
        },
        "primary": {
            "outcome": "werewolf-team win",
            "condition_summary": condition_summary,
            "risk_difference_P+_minus_P-PP": by_condition["P+"]["proportion"] - by_condition["P-PP"]["proportion"],
            "risk_ratio_P+_over_P-PP": risk_ratio(
                by_condition["P+"]["numerator"],
                by_condition["P+"]["denominator"],
                by_condition["P-PP"]["numerator"],
                by_condition["P-PP"]["denominator"],
            ),
            "paired_comparison": paired,
        },
        "multiplicity": {
            "method": "Holm, two-sided family-wise alpha 0.05",
            "raw_p_values": {
                "fixed_board_composite_pp_success": fixed_p,
                "normal_game_werewolf_team_win_rate": paired["exact_mcnemar_p_value"],
            },
            "holm_adjusted_p_values": adjusted,
            "fixed_board_effect": {
                "difference": fixed["primary_comparison"]["paired_risk_difference"],
                "ci95": fixed["primary_comparison"]["cluster_bootstrap95"],
            },
        },
        "secondary": {
            "pp_reach": reach_rows,
            "pp_reach_paired_comparison": paired_summary(
                paired_values(records, "target_pp_reached"),
                iterations=args.bootstrap_iterations,
                seed=args.bootstrap_seed + 3,
            ),
            "post_reach": post_rows,
            "post_reach_caveat": (
                "selected outcome conditional on target survival to a true PP board; composite scoring is a post-hoc "
                "deterministic live-log operationalization, not the blinded fixed-board judge endpoint"
            ),
            "condition_role_summary": group(records, ("condition", "actual_role")),
            "condition_seat_summary": group(records, ("condition", "target_seat")),
            "condition_block_summary": group(records, ("condition", "analysis_block")),
            "condition_wolf_ally_team_summary": group(
                [
                    {
                        **record,
                        "wolf_ally_team": next(
                            agent["team"]
                            for agent in json.loads(
                                saved_game_path(run_dir, finishes[record["row_id"]]["server_json"]).read_text(),
                            )["agents"]
                            if agent["role"] in WOLF_ROLES and agent["team"] != "yatolab"
                        ),
                    }
                    for record in records
                ],
                ("condition", "wolf_ally_team"),
            ),
            "role_paired_comparisons_descriptive": role_pairs,
        },
        "sensitivity": {
            "matching": matching,
            "pair_stratified_conditional_odds_ratio": conditional_odds_ratio(
                paired_table["plus_only"],
                paired_table["minus_only"],
            ),
            "p_value": paired["exact_mcnemar_p_value"],
            "note": (
                "The pair-stratified analysis is already adjusted for role, opponent pool, seat, execution block, "
                "and optimizer seed because every factor is constant within each match pair. Pool O has one level."
            ),
        },
        "target_token_usage": {
            "condition_totals": target_tokens,
            "paired_input_token_difference_P+_minus_P-PP": {
                "mean": sum(input_differences) / len(input_differences),
                "median": quantile([float(value) for value in input_differences], 0.5),
                "minimum": min(input_differences),
                "maximum": max(input_differences),
            },
        },
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    analysis_path = output_dir / "analysis.json"
    analysis_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_games(output_dir / "games.csv", records)
    write_table(output_dir / "tables.md", result)
    write_svg(output_dir / "effect-sizes.svg", result)
    manifest = {
        "analysis_sha256": sha256(analysis_path),
        "games_sha256": sha256(output_dir / "games.csv"),
        "tables_sha256": sha256(output_dir / "tables.md"),
        "effect_sizes_svg_sha256": sha256(output_dir / "effect-sizes.svg"),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"output={output_dir} eligible={len(records)} "
        f"win_rd={result['primary']['risk_difference_P+_minus_P-PP']:.6f} "
        f"mcnemar_p={paired['exact_mcnemar_p_value']:.6f}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
