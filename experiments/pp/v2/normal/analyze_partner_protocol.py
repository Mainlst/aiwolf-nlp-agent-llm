#!/usr/bin/env python3
"""Reproduce the post-hoc fixed-partner protocol audit from saved game logs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUN_DIR = Path("experiments/runs/pp-v2-normal-400-clean-20260815-v1")
DEFAULT_GAMES_CSV = Path(
    "experiments/pp/v2/normal/results/pp-v2-normal-400-clean-20260815-v1/games.csv",
)
DEFAULT_OUTPUT = Path(
    "experiments/pp/v2/normal/results/pp-v2-normal-400-clean-20260815-v1/"
    "partner-protocol-audit.json",
)
CELL_ORDER = (
    ("POSSESSED", "P+"),
    ("POSSESSED", "P-PP"),
    ("WEREWOLF", "P+"),
    ("WEREWOLF", "P-PP"),
)


def sha256_bytes(data: bytes) -> str:
    """Return a lowercase SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    """Hash one input file."""
    return sha256_bytes(path.read_bytes())


def resolve(path: Path) -> Path:
    """Resolve CLI paths relative to the repository rather than the caller's cwd."""
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def display_path(path: Path) -> str:
    """Use stable repository-relative paths in the deterministic artifact."""
    try:
        return path.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_csv(path: Path) -> dict[str, dict[str, str]]:
    """Read a CSV keyed by unique row_id, failing on malformed inputs."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(not row.get("row_id") for row in rows):
        raise ValueError(f"{path}: missing rows or row_id")
    keyed = {row["row_id"]: row for row in rows}
    if len(keyed) != len(rows):
        raise ValueError(f"{path}: duplicate row_id")
    return keyed


def parse_bool(value: str, *, field: str, row_id: str) -> bool:
    """Parse the explicit booleans written by the saved normal-game analysis."""
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"{row_id}: invalid {field}={value!r}")


def load_saved_parser(path: Path) -> Callable[[Path], dict[str, Any]]:
    """Load parse_game_log from the protocol snapshot archived with the run."""
    spec = importlib.util.spec_from_file_location("_saved_pp_log_parser", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load parser from {path}")
    module = importlib.util.module_from_spec(spec)
    loader: Any = spec.loader
    loader.exec_module(module)
    typed_module: ModuleType = module
    parse_game_log = getattr(typed_module, "parse_game_log", None)
    if not callable(parse_game_log):
        raise ImportError(f"{path}: parse_game_log is unavailable")
    return parse_game_log


def selected_log(run_dir: Path, row_id: str, game_id: str) -> Path:
    """Select the analyzed attempt by game_id, not by arbitrary glob order."""
    matches = sorted((run_dir / "server-logs" / row_id).glob(f"run-*/game/{game_id}.log"))
    if len(matches) != 1:
        raise ValueError(
            f"{row_id}: expected exactly one saved log for analyzed game {game_id}, found {len(matches)}",
        )
    return matches[0]


def empty_cell() -> Counter[str]:
    """Create counters used for one target-role by condition cell."""
    return Counter(
        games=0,
        reached=0,
        wins_after_reach=0,
        target_true_role_co=0,
        partner_true_role_co=0,
        target_vote_village=0,
        target_vote_partner=0,
        partner_vote_village=0,
        partner_vote_target=0,
        wins_target_vote_village=0,
        wins_target_vote_partner=0,
        wins_partner_vote_village=0,
        wins_partner_vote_target=0,
    )


def create_artifact(run_dir: Path, games_csv: Path) -> dict[str, Any]:
    """Join saved inputs, validate them, and aggregate the four audit cells."""
    assignments_path = run_dir / "assignments.csv"
    parser_path = run_dir / "protocol" / "pp_log_parser.py"
    assignments = read_csv(assignments_path)
    published = read_csv(games_csv)
    if assignments.keys() != published.keys():
        missing_results = sorted(assignments.keys() - published.keys())
        missing_assignments = sorted(published.keys() - assignments.keys())
        raise ValueError(
            "assignment/result row mismatch: "
            f"missing_results={missing_results}, missing_assignments={missing_assignments}",
        )
    if len(assignments) != 400:
        raise ValueError(f"expected 400 analyzed rows, found {len(assignments)}")

    parse_game_log = load_saved_parser(parser_path)
    cells = {key: empty_cell() for key in CELL_ORDER}
    board_counts: Counter[int] = Counter()
    selected_game_ids: set[str] = set()
    log_manifest_lines: list[str] = []

    for row_id in sorted(assignments):
        assignment = assignments[row_id]
        result = published[row_id]
        for field in ("condition", "planned_role", "target_seat", "analysis_block", "match_pair_id"):
            if assignment[field] != result[field]:
                raise ValueError(
                    f"{row_id}: assignment/result {field} mismatch "
                    f"({assignment[field]!r} != {result[field]!r})",
                )

        condition = assignment["condition"]
        target_role = assignment["planned_role"]
        key = (target_role, condition)
        if key not in cells:
            raise ValueError(f"{row_id}: unexpected audit cell {key}")
        cells[key]["games"] += 1

        game_id = result["game_id"]
        if not game_id or game_id in selected_game_ids:
            raise ValueError(f"{row_id}: missing or duplicate analyzed game_id={game_id!r}")
        selected_game_ids.add(game_id)
        log_path = selected_log(run_dir, row_id, game_id)
        log_manifest_lines.append(f"{row_id}\t{game_id}\t{sha256(log_path)}")
        game = parse_game_log(log_path)
        if log_path.stem != game_id:
            raise ValueError(f"{row_id}: selected log filename disagrees with games.csv")

        target = int(assignment["target_seat"])
        if game["roles"].get(target) != target_role:
            raise ValueError(
                f"{row_id}: target seat {target} has role {game['roles'].get(target)!r}, "
                f"expected {target_role}",
            )
        parsed_win = game["win_side"] == "WEREWOLF"
        if game["win_side"] != result["win_side"] or parsed_win != parse_bool(
            result["won"],
            field="won",
            row_id=row_id,
        ):
            raise ValueError(f"{row_id}: saved outcome disagrees with games.csv")

        boards = game["pp_boards"]
        board_counts[len(boards)] += 1
        game_reached = bool(boards)
        target_reached = bool(boards and target in boards[0]["alive"])
        if game_reached != parse_bool(result["game_pp_reached"], field="game_pp_reached", row_id=row_id):
            raise ValueError(f"{row_id}: game_pp_reached disagrees with games.csv")
        if target_reached != parse_bool(
            result["target_pp_reached"],
            field="target_pp_reached",
            row_id=row_id,
        ):
            raise ValueError(f"{row_id}: target_pp_reached disagrees with games.csv")
        if not target_reached:
            continue
        if len(boards) != 1:
            raise ValueError(f"{row_id}: expected one true PP board after target reach, found {len(boards)}")

        board = boards[0]
        day = int(board["day"])
        village = int(board["village_target"])
        partner = int(board["possessed"] if target_role == "WEREWOLF" else board["werewolf"])
        expected_partner_role = "POSSESSED" if target_role == "WEREWOLF" else "WEREWOLF"
        if game["roles"].get(partner) != expected_partner_role:
            raise ValueError(f"{row_id}: incorrect partner role on reached PP board")

        day_votes = game["votes"].get(day, {})
        target_vote = day_votes.get(target)
        partner_vote = day_votes.get(partner)
        if target_vote not in {village, partner}:
            raise ValueError(f"{row_id}: target vote {target_vote!r} is neither village survivor nor partner")
        if partner_vote not in {village, target}:
            raise ValueError(f"{row_id}: partner vote {partner_vote!r} is neither village survivor nor target")

        cell = cells[key]
        cell["reached"] += 1
        cell["wins_after_reach"] += parsed_win
        cell["target_vote_village"] += target_vote == village
        cell["target_vote_partner"] += target_vote == partner
        cell["partner_vote_village"] += partner_vote == village
        cell["partner_vote_target"] += partner_vote == target
        cell["wins_target_vote_village"] += parsed_win and target_vote == village
        cell["wins_target_vote_partner"] += parsed_win and target_vote == partner
        cell["wins_partner_vote_village"] += parsed_win and partner_vote == village
        cell["wins_partner_vote_target"] += parsed_win and partner_vote == target
        cell["target_true_role_co"] += any(
            co["day"] == day
            and co["agent"] == target
            and co["claimed_role"] == target_role
            for co in game["role_cos"]
        )
        cell["partner_true_role_co"] += any(
            co["day"] == day
            and co["agent"] == partner
            and co["claimed_role"] == expected_partner_role
            for co in game["role_cos"]
        )

    if any(cells[key]["games"] != 100 for key in CELL_ORDER):
        raise ValueError(f"expected 100 games in every role-condition cell: {cells}")
    if board_counts != Counter({0: 295, 1: 105}):
        raise ValueError(f"unexpected true-PP-board distribution: {dict(board_counts)}")

    rows = []
    for target_role, condition in CELL_ORDER:
        cell = cells[(target_role, condition)]
        reached = cell["reached"]
        if cell["target_vote_village"] + cell["target_vote_partner"] != reached:
            raise ValueError(f"{target_role}/{condition}: target-vote partition does not sum to reach")
        if cell["partner_vote_village"] + cell["partner_vote_target"] != reached:
            raise ValueError(f"{target_role}/{condition}: partner-vote partition does not sum to reach")
        rows.append(
            {
                "condition": condition,
                "target_role": target_role,
                "games": cell["games"],
                "pp_reached": {"numerator": reached, "denominator": cell["games"]},
                "win_after_reach": {"numerator": cell["wins_after_reach"], "denominator": reached},
                "target_true_role_co_after_reach": {
                    "numerator": cell["target_true_role_co"],
                    "denominator": reached,
                },
                "partner_true_role_co_after_reach": {
                    "numerator": cell["partner_true_role_co"],
                    "denominator": reached,
                },
                "target_final_vote_after_reach": {
                    "village_survivor": cell["target_vote_village"],
                    "partner": cell["target_vote_partner"],
                    "denominator": reached,
                },
                "partner_final_vote_after_reach": {
                    "village_survivor": cell["partner_vote_village"],
                    "target": cell["partner_vote_target"],
                    "denominator": reached,
                },
                "wins_by_target_final_vote_after_reach": {
                    "village_survivor": cell["wins_target_vote_village"],
                    "partner": cell["wins_target_vote_partner"],
                },
                "wins_by_partner_final_vote_after_reach": {
                    "village_survivor": cell["wins_partner_vote_village"],
                    "target": cell["wins_partner_vote_target"],
                },
            },
        )

    role_aggregates = []
    for target_role in ("POSSESSED", "WEREWOLF"):
        role_cells = [cells[(target_role, condition)] for condition in ("P+", "P-PP")]
        reached = sum(cell["reached"] for cell in role_cells)
        role_aggregates.append(
            {
                "target_role": target_role,
                "reached": reached,
                "partner_true_role_co": sum(cell["partner_true_role_co"] for cell in role_cells),
            },
        )

    log_manifest = ("\n".join(log_manifest_lines) + "\n").encode("utf-8")
    return {
        "schema_version": 1,
        "analysis": "post_hoc_fixed_partner_protocol_audit",
        "caveat": (
            "Descriptive post-hoc audit. Reach is treatment-dependent; outcomes and behaviors "
            "conditional on reach are not causal effects or mediation estimates."
        ),
        "definitions": {
            "board": (
                "The first saved day with exactly one living WEREWOLF, one living POSSESSED, "
                "and one living village-team player."
            ),
            "partner": "The non-target werewolf-team survivor, which uses the fixed O setting.",
            "partner_true_role_co": (
                "At least one reach-day role_cos event emitted by the archived deterministic parser "
                "whose agent is the partner and whose claimed_role equals the partner's true role."
            ),
            "target_true_role_co": (
                "At least one reach-day role_cos event emitted by the archived deterministic parser "
                "whose agent is the target and whose claimed_role equals the target's true role."
            ),
            "votes": "Final server-recorded votes on the reached PP day.",
            "wins_by_vote": (
                "Werewolf-team wins cross-tabulated by each agent's final server-recorded vote; "
                "the categories use the same denominators as the corresponding vote counts."
            ),
        },
        "provenance": {
            "generator": {
                "path": display_path(Path(__file__).resolve()),
                "sha256": sha256(Path(__file__).resolve()),
            },
            "run_id": run_dir.name,
            "assignments": {
                "path": display_path(assignments_path),
                "sha256": sha256(assignments_path),
            },
            "games_csv": {"path": display_path(games_csv), "sha256": sha256(games_csv)},
            "saved_pp_log_parser": {
                "path": display_path(parser_path),
                "sha256": sha256(parser_path),
            },
            "selected_game_logs": {
                "count": len(log_manifest_lines),
                "manifest_sha256": sha256_bytes(log_manifest),
                "manifest_format": "sorted row_id<TAB>game_id<TAB>file_sha256, newline terminated",
            },
        },
        "validation": {
            "assignment_rows": len(assignments),
            "games_csv_rows": len(published),
            "unique_selected_game_ids": len(selected_game_ids),
            "games_by_true_pp_board_count": {
                str(count): board_counts[count] for count in sorted(board_counts)
            },
            "target_reached_games": sum(cell["reached"] for cell in cells.values()),
            "all_saved_fields_matched": True,
        },
        "cells": rows,
        "target_role_aggregates": role_aggregates,
    }


def main() -> int:
    """Write or byte-check the deterministic audit artifact."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--games-csv", type=Path, default=DEFAULT_GAMES_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare regenerated bytes with --output without rewriting it",
    )
    args = parser.parse_args()
    run_dir = resolve(args.run_dir)
    games_csv = resolve(args.games_csv)
    output = resolve(args.output)
    artifact = create_artifact(run_dir, games_csv)
    encoded = (json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    if args.check:
        if not output.is_file():
            raise FileNotFoundError(f"cannot check missing artifact: {output}")
        if output.read_bytes() != encoded:
            raise ValueError(f"regenerated artifact differs from {output}")
        print(f"matched={display_path(output)} sha256={sha256_bytes(encoded)}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    print(f"wrote={display_path(output)} sha256={sha256_bytes(encoded)}")
    for row in artifact["cells"]:
        print(
            row["target_role"],
            row["condition"],
            f"reach={row['pp_reached']['numerator']}/{row['pp_reached']['denominator']}",
            f"win={row['win_after_reach']['numerator']}/{row['win_after_reach']['denominator']}",
            "partner_co="
            f"{row['partner_true_role_co_after_reach']['numerator']}/"
            f"{row['partner_true_role_co_after_reach']['denominator']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
