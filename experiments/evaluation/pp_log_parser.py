#!/usr/bin/env python3
"""Deterministic PP event extraction from AIWolf CSV-like game logs."""

# ruff: noqa: C901, PLR0912, PLR0915, PLR2004, TC003

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

WOLF_ROLES = {"WEREWOLF", "POSSESSED"}
VILLAGE_ROLES = {"VILLAGER", "SEER", "MEDIUM", "BODYGUARD"}


def _parse_talk(line: str) -> dict[str, Any] | None:
    """Parse an unescaped natural-language TALK line from both ends."""
    head = line.split(",", 5)
    if len(head) != 6 or head[1] != "talk":
        return None
    try:
        text, timestamp = head[5].rsplit(",", 1)
        return {
            "day": int(head[0]),
            "index": int(head[2]),
            "turn": int(head[3]),
            "agent": int(head[4]),
            "text": text,
            "timestamp": int(timestamp),
        }
    except (ValueError, TypeError):
        return None


def _role_co(text: str) -> str | None:
    """Return an explicit first-person wolf-side role CO, if present."""
    normalized = text.replace("\u2019", "'")
    for role, name in (("POSSESSED", "possessed"), ("WEREWOLF", "werewolf"), ("WEREWOLF", "wolf")):
        pattern = rf"\bI\s*(?:am|'m)\s+(?:the\s+)?{name}\b"
        if re.search(pattern, normalized, re.IGNORECASE):
            return role
    return None


def _declared_vote_target(text: str, names: dict[int, str]) -> int | None:
    """Extract the last unambiguous named vote declaration from one utterance."""
    if not names:
        return None
    by_name = {name.casefold(): index for index, name in names.items()}
    alternatives = "|".join(re.escape(name) for name in sorted(by_name, key=len, reverse=True))
    pattern = re.compile(
        rf"(?:\bvote(?:\s+(?:is|remains|stays))?(?:\s+(?:for|on))?"
        rf"|\bvoting(?:\s+for)?|\bmatch(?:\s+my)?\s+vote(?:\s+on)?|\bunite\s+on)"
        rf"\s+@?\s*(?P<target>{alternatives})\b",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    for match in reversed(matches):
        prefix = text[max(0, match.start() - 14) : match.start()].casefold()
        if "don't" in prefix or "do not" in prefix:
            continue
        return by_name[match.group("target").casefold()]
    return None


def parse_game_log(path: Path) -> dict[str, Any]:
    """Parse roles, results, PP boards, COs, votes, and TALK/VOTE joins."""
    statuses: dict[int, dict[int, str]] = defaultdict(dict)
    roles: dict[int, str] = {}
    names: dict[int, str] = {}
    teams: dict[int, str] = {}
    talks: list[dict[str, Any]] = []
    votes: dict[int, dict[int, int]] = defaultdict(dict)
    win_side: str | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split(",", 6)
        if len(fields) >= 7 and fields[1] == "status":
            day = int(fields[0])
            agent = int(fields[2])
            roles[agent] = fields[3]
            statuses[day][agent] = fields[4]
            teams[agent] = fields[5]
            names[agent] = fields[6]
            continue
        talk = _parse_talk(line)
        if talk is not None:
            talks.append(talk)
            continue
        fields = line.split(",")
        if len(fields) >= 4 and fields[1] == "vote":
            votes[int(fields[0])][int(fields[2])] = int(fields[3])
        elif len(fields) >= 3 and fields[1] == "result":
            win_side = fields[-1].upper()

    declarations: dict[tuple[int, int], int] = {}
    role_cos: list[dict[str, Any]] = []
    for talk in talks:
        key = (talk["day"], talk["agent"])
        target = _declared_vote_target(talk["text"], names)
        if target is not None:
            declarations[key] = target
        claimed_role = _role_co(talk["text"])
        if claimed_role is not None:
            role_cos.append(
                {
                    "event_id": f"d{talk['day']}-talk-{talk['index']}",
                    "day": talk["day"],
                    "agent": talk["agent"],
                    "claimed_role": claimed_role,
                },
            )

    pp_boards: list[dict[str, Any]] = []
    for day, day_statuses in sorted(statuses.items()):
        alive = sorted(agent for agent, status in day_statuses.items() if status == "ALIVE")
        alive_roles = [roles[agent] for agent in alive]
        if len(alive) != 3 or alive_roles.count("WEREWOLF") != 1 or alive_roles.count("POSSESSED") != 1:
            continue
        village = [agent for agent in alive if roles[agent] in VILLAGE_ROLES]
        if len(village) != 1:
            continue
        wolf = next(agent for agent in alive if roles[agent] == "WEREWOLF")
        possessed = next(agent for agent in alive if roles[agent] == "POSSESSED")
        wolf_vote = votes.get(day, {}).get(wolf)
        possessed_vote = votes.get(day, {}).get(possessed)
        pp_boards.append(
            {
                "day": day,
                "alive": alive,
                "werewolf": wolf,
                "possessed": possessed,
                "village_target": village[0],
                "vote_coordinated": wolf_vote == possessed_vote == village[0],
            },
        )

    talk_vote_joins = []
    for day, day_votes in sorted(votes.items()):
        for agent, actual_target in sorted(day_votes.items()):
            declared_target = declarations.get((day, agent))
            talk_vote_joins.append(
                {
                    "day": day,
                    "agent": agent,
                    "declared_target": declared_target,
                    "actual_target": actual_target,
                    "matched": declared_target == actual_target if declared_target is not None else None,
                },
            )

    return {
        "game_id": path.name.split("_", 1)[0],
        "win_side": win_side,
        "roles": roles,
        "names": names,
        "teams": teams,
        "statuses": dict(statuses),
        "talks": talks,
        "votes": dict(votes),
        "role_cos": role_cos,
        "pp_boards": pp_boards,
        "talk_vote_joins": talk_vote_joins,
    }
