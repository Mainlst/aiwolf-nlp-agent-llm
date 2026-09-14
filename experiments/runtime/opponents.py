#!/usr/bin/env python3
"""API-free deterministic opponents for Stage 0 and the 20-game pilot."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable

from aiwolf_nlp_common.client import Client
from aiwolf_nlp_common.packet import Info, Packet, Request, Role, Status

TEAM_STEMS = ("alpha", "bravo", "charlie", "delta", "echo")
RESPONSE_REQUESTS = {
    Request.NAME,
    Request.TALK,
    Request.WHISPER,
    Request.VOTE,
    Request.DIVINE,
    Request.GUARD,
    Request.ATTACK,
}


class RuleOpponent:
    """Small deterministic policy with no model or network dependency beyond the game server."""

    def __init__(self, pool: str, original_name: str) -> None:
        """Initialize one independently named opponent."""
        self.pool = pool
        self.original_name = original_name
        self.info: Info | None = None
        self.role: Role | None = None
        self.talk_counts: dict[int, int] = {}
        self.vote_target: str = ""

    def update(self, packet: Packet) -> None:
        """Update the latest server state."""
        if packet.info is None:
            return
        self.info = packet.info
        self.role = packet.info.role_map.get(packet.info.agent, self.role)

    def living_targets(self) -> list[str]:
        """Return sorted living players other than self."""
        if self.info is None:
            return []
        return sorted(
            name for name, status in self.info.status_map.items() if status == Status.ALIVE and name != self.info.agent
        )

    def target(self, salt: str) -> str:
        """Choose reproducibly from the current legal targets."""
        targets = self.living_targets()
        if not targets:
            return ""
        game_id = self.info.game_id if self.info else ""
        key = f"{game_id}|{self.original_name}|{self.pool}|{salt}".encode()
        index = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % len(targets)
        return targets[index]

    def talk(self) -> str:
        """Generate short policy-distinct English talk."""
        if self.info is None:
            return "Skip"
        day = self.info.day
        count = self.talk_counts.get(day, 0)
        self.talk_counts[day] = count + 1
        target = self.target(f"talk-{day}-{count}")
        self.vote_target = target or self.vote_target
        if day == 0:
            return "Hello. I will compare claims and votes carefully." if count == 0 else "Skip"

        if self.role == Role.SEER and self.info.divine_result is not None and count == 0:
            result = str(self.info.divine_result.result).split(".")[-1]
            return f"I am the seer. {self.info.divine_result.target} was {result}."
        if self.role == Role.POSSESSED and count == 0:
            return f"I am the seer. {target} was HUMAN."
        if not target:
            return "Skip"
        if self.pool == "A":
            variants = (
                f"{target}. Who do you suspect and why?",
                f"I suspect {target} because their vote position is still unclear.",
                f"I plan to vote for {target}. Does anyone have a stronger case?",
            )
        elif self.pool == "B":
            variants = (
                f"I suspect {target}. Their position does not match the discussion.",
                f"My vote is {target}. Why does your position match the discussion?",
                f"We should compare {target}'s claim with their actual vote.",
            )
        else:
            variants = (
                f"I am not decided. {target}. Who will you vote for?",
                f"I slightly suspect {target} but want one concrete reason before voting.",
                "Skip",
            )
        return variants[count % len(variants)]

    def respond(self, request: Request) -> str:
        """Return a legal response for one server request."""
        if request == Request.NAME:
            return self.original_name
        if request == Request.TALK:
            return self.talk()
        if request == Request.WHISPER:
            return "Skip"
        if request == Request.VOTE:
            target = self.vote_target if self.vote_target in self.living_targets() else self.target("vote")
            return target
        if request == Request.DIVINE:
            return self.target("divine")
        if request == Request.GUARD:
            return self.target("guard")
        if request == Request.ATTACK:
            return self.target("attack")
        return ""


def append_log(path: Path | None, lock: threading.Lock, payload: dict[str, Any]) -> None:
    """Append one opponent audit event."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def run_client(
    url: str,
    policy: RuleOpponent,
    log_path: Path | None,
    lock: threading.Lock,
    connect_retries: int,
) -> None:
    """Connect one opponent and serve exactly one game."""
    client = Client(url=url, token=None)
    try:
        for attempt in range(connect_retries):
            try:
                client.connect()
                break
            except Exception:
                if attempt + 1 == connect_retries:
                    raise
                time.sleep(0.5)
        while True:
            packet = client.receive()
            policy.update(packet)
            response = policy.respond(packet.request)
            append_log(
                log_path,
                lock,
                {
                    "timestamp": datetime.now(UTC).astimezone().isoformat(),
                    "pool": policy.pool,
                    "original_name": policy.original_name,
                    "game_id": policy.info.game_id if policy.info else None,
                    "role": str(policy.role) if policy.role else None,
                    "request": str(packet.request),
                    "response": response if packet.request in RESPONSE_REQUESTS else None,
                },
            )
            if packet.request in RESPONSE_REQUESTS:
                client.send(response)
            if packet.request == Request.FINISH:
                break
    finally:
        client.close()


def capture_thread_error(action: Callable[[], None], errors: list[BaseException], lock: threading.Lock) -> None:
    """Run a thread action and make failures visible to the parent process."""
    try:
        action()
    except BaseException as error:  # noqa: BLE001
        with lock:
            errors.append(error)


def main() -> int:
    """Launch four pool members, or five for an API-free Stage 0 match."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", required=True, choices=("A", "B", "C"))
    parser.add_argument("--url", default="ws://127.0.0.1:8080/ws")
    parser.add_argument("--count", type=int, choices=(4, 5), default=4)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--connect-retries", type=int, default=30)
    args = parser.parse_args()
    lock = threading.Lock()
    threads = []
    errors: list[BaseException] = []
    for stem in TEAM_STEMS[: args.count]:
        original_name = f"pilot{args.pool}{stem}1"
        policy = RuleOpponent(args.pool, original_name)
        action = lambda policy=policy: run_client(args.url, policy, args.log, lock, args.connect_retries)
        thread = threading.Thread(target=capture_thread_error, args=(action, errors, lock), name=original_name)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    if errors:
        raise RuntimeError(f"{len(errors)} opponent client(s) failed") from errors[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
