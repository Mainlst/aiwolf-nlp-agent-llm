#!/usr/bin/env python3
"""Export all public TALK events from one AIWolf game log to Markdown."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from pathlib import Path


def main() -> int:
    """Create a chronological, human-readable whole-game conversation log."""
    parser = argparse.ArgumentParser()
    parser.add_argument("game_log", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    players: dict[int, dict[str, str]] = {}
    talks: list[dict[str, str | int]] = []
    win_side = "UNKNOWN"
    for raw_line in args.game_log.read_text(encoding="utf-8").splitlines():
        if not raw_line:
            continue
        fields = raw_line.split(",")
        if len(fields) >= 7 and fields[1] == "status":
            agent_index = int(fields[2])
            players[agent_index] = {
                "role": fields[3],
                "team": fields[5],
                "name": fields[6],
            }
        elif len(fields) >= 7 and fields[1] == "talk":
            body, timestamp = raw_line.rsplit(",", 1)
            day, _, index, turn, agent, text = body.split(",", 5)
            talks.append(
                {
                    "day": int(day),
                    "index": int(index),
                    "turn": int(turn),
                    "agent": int(agent),
                    "text": text,
                    "timestamp": timestamp,
                },
            )
        elif len(fields) >= 3 and fields[1] == "result":
            win_side = fields[-1]

    game_id = args.game_log.stem
    source_hash = hashlib.sha256(args.game_log.read_bytes()).hexdigest()
    day_counts = Counter(int(talk["day"]) for talk in talks)
    lines = [
        f"# Whole-game TALK log — {game_id}",
        "",
        f"- Source: `{args.game_log}`",
        f"- Source SHA-256: `{source_hash}`",
        f"- Winning side: `{win_side}`",
        f"- Public TALK events: {len(talks)}",
        "",
        "## Players",
        "",
        "| Agent | Name | Role | Client team |",
        "| ---: | --- | --- | --- |",
    ]
    for agent_index, player in sorted(players.items()):
        lines.append(f"| {agent_index} | {player['name']} | {player['role']} | {player['team']} |")

    current_day: int | None = None
    for talk in talks:
        day = int(talk["day"])
        if day != current_day:
            current_day = day
            lines.extend(["", f"## Day {day} ({day_counts[day]} TALK events)", ""])
        agent_index = int(talk["agent"])
        player = players.get(agent_index, {"name": f"Agent {agent_index}", "role": "UNKNOWN", "team": "UNKNOWN"})
        lines.append(
            f"- `{talk['index']:02d}` turn `{talk['turn']}` — "
            f"**{player['name']}** (Agent {agent_index}, {player['role']}, `{player['team']}`): {talk['text']}",
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"talks={len(talks)} days={len(day_counts)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
