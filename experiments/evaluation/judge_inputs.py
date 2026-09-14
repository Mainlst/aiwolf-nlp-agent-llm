#!/usr/bin/env python3
"""Create deterministic, future-safe, blind judge inputs from normalized units."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import re
from typing import Any

HIDDEN_KEYS = {
    "agent_name",
    "condition",
    "condition_id",
    "game_id",
    "guard_enabled",
    "hypothesis",
    "outcome",
    "prompt",
    "team",
    "win_side",
    "winner",
}
EVENT_KEYS = {"day", "event_id", "result", "sequence", "speaker", "target", "text", "type"}


def _digest(value: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}|{value}".encode()).hexdigest()


def _replace_names(text: str, mapping: dict[str, str]) -> str:
    output = text
    for original in sorted(mapping, key=len, reverse=True):
        output = re.sub(rf"(?<![\w-]){re.escape(original)}(?![\w-])", mapping[original], output)
    return output


def _player_mapping(unit: dict[str, Any], seed: str) -> dict[str, str]:
    players = {str(value) for value in unit.get("players", []) if value}
    target = unit.get("target_agent")
    if target:
        players.add(str(target))
    for event in unit.get("events", []):
        for key in ("speaker", "target"):
            value = event.get(key)
            if value and value != "server":
                players.add(str(value))
    ordered = sorted(players, key=lambda value: _digest(value, seed))
    return {name: f"Player-{index}" for index, name in enumerate(ordered, 1)}


def anonymize_unit(
    unit: dict[str, Any], seed: str = "aiwolfdial-2026-blind-v1"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a blind unit and a separate private re-identification record."""
    required = {"unit_id", "metric", "target_agent", "cutoff_sequence", "events"}
    missing = sorted(required - unit.keys())
    if missing:
        raise ValueError(f"Missing input fields: {missing}")
    cutoff = unit["cutoff_sequence"]
    if not isinstance(cutoff, int):
        raise ValueError("cutoff_sequence must be an integer")
    mapping = _player_mapping(unit, seed)
    blind_id = _digest(str(unit["unit_id"]), seed)[:24]
    history: list[dict[str, Any]] = []
    for raw_event in unit["events"]:
        if raw_event.get("public", True) is not True:
            continue
        sequence = raw_event.get("sequence")
        if not isinstance(sequence, int) or sequence > cutoff:
            continue
        event = {key: raw_event[key] for key in EVENT_KEYS if key in raw_event}
        event.setdefault("event_id", f"event-{sequence}")
        for key in ("speaker", "target"):
            if key in event and event[key] in mapping:
                event[key] = mapping[event[key]]
        if isinstance(event.get("text"), str):
            event["text"] = _replace_names(event["text"], mapping)
        history.append(event)
    history.sort(key=lambda event: event["sequence"])

    metric = str(unit["metric"])
    blind: dict[str, Any] = {
        "schema_version": 1,
        "blind_id": blind_id,
        "metric": metric,
        "target_player": mapping[str(unit["target_agent"])],
        "public_history": history,
        "evaluation_target_event_id": unit.get("evaluation_target_event_id"),
    }
    if metric == "role_action_quality":
        blind["target_role"] = unit.get("target_role")
    private = {
        "blind_id": blind_id,
        "unit_id": unit["unit_id"],
        "game_id": unit.get("game_id"),
        "condition": unit.get("condition", unit.get("condition_id")),
        "outcome": unit.get("outcome"),
        "role": unit.get("target_role", unit.get("role")),
        "opponent_pool": unit.get("opponent_pool"),
        "player_mapping": mapping,
    }
    assert_blind(blind, mapping)
    return blind, private


def assert_blind(value: Any, mapping: dict[str, str] | None = None) -> None:
    """Reject hidden fields or original player names anywhere in a blind payload."""
    serialized = json.dumps(value, ensure_ascii=False)
    if mapping:
        leaked = [name for name in mapping if name in serialized]
        if leaked:
            raise ValueError(f"Original player names leaked: {leaked}")

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            hidden = HIDDEN_KEYS.intersection(item)
            if hidden:
                raise ValueError(f"Hidden fields leaked: {sorted(hidden)}")
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)


def select_stratified(records: list[dict[str, Any]], maximum: int, seed: str) -> list[dict[str, Any]]:
    """Select a deterministic round-robin sample across preregistered strata."""
    if len(records) <= maximum:
        return records
    strata: dict[tuple[str, ...], deque[dict[str, Any]]] = defaultdict(deque)
    for record in records:
        private = record["private"]
        key = tuple(str(private.get(name, "")) for name in ("condition", "role", "opponent_pool")) + (
            str(record["blind"]["metric"]),
        )
        strata[key].append(record)
    for key, values in strata.items():
        ordered = sorted(values, key=lambda item: _digest(item["blind"]["blind_id"], f"{seed}|{key}"))
        strata[key] = deque(ordered)
    output: list[dict[str, Any]] = []
    keys = sorted(strata)
    while len(output) < maximum:
        progressed = False
        for key in keys:
            if strata[key] and len(output) < maximum:
                output.append(strata[key].popleft())
                progressed = True
        if not progressed:
            break
    return output


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--private-map", required=True, type=Path)
    parser.add_argument("--seed", default="aiwolfdial-2026-blind-v1")
    parser.add_argument("--maximum-units", type=int)
    args = parser.parse_args()
    combined = []
    for unit in _read_jsonl(args.input):
        blind, private = anonymize_unit(unit, args.seed)
        combined.append({"blind": blind, "private": private})
    if args.maximum_units is not None:
        combined = select_stratified(combined, args.maximum_units, args.seed)
    _write_jsonl(args.output, [item["blind"] for item in combined])
    _write_jsonl(args.private_map, [item["private"] for item in combined])
    print(f"blind_units={len(combined)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
