#!/usr/bin/env python3
"""Generate deterministic synthetic PP and near-control decision snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SPEC_PATH = HERE / "spec.yml"

NAMES = (
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
SUSPICIONS = (
    "avoided naming a vote",
    "changed targets after the claim",
    "defended the late report",
    "ignored the attack implication",
    "followed the largest wagon",
    "contradicted yesterday's vote",
    "pressed the cleared player",
    "left the seer comparison vague",
)
CLAIM_REASONS = (
    "the first report came before any pressure",
    "the second report copied the same target",
    "the vote did not match the stated suspicion",
    "the attack removed the earlier claimant",
    "the innocent result protected the loudest voter",
    "the guilty result appeared only after rebuttal",
)


def canonical(value: Any) -> bytes:
    """Return canonical JSON bytes for hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_value(value: Any) -> str:
    """Hash one JSON-compatible value."""
    return hashlib.sha256(canonical(value)).hexdigest()


def event(day: int, sequence: int, kind: str, **fields: Any) -> dict[str, Any]:
    """Build one public event with a stable event ID."""
    return {"event_id": f"d{day}-e{sequence:02d}", "day": day, "sequence": sequence, "type": kind, **fields}


def names_for(index: int, offset: int) -> list[str]:
    """Choose five deterministic names without embedding a synthetic nonce."""
    start = (index * 7 + offset * 11) % len(NAMES)
    return [NAMES[(start + step * 5) % len(NAMES)] for step in range(5)]


def role_assignment(names: list[str], perspective_role: str, index: int) -> tuple[dict[str, str], str]:
    """Rotate all roles and return hidden roles plus the perspective name."""
    roles = [
        perspective_role,
        "WEREWOLF" if perspective_role == "POSSESSED" else "POSSESSED",
        "SEER",
        "VILLAGER",
        "VILLAGER",
    ]
    shift = index % len(names)
    rotated = names[shift:] + names[:shift]
    return dict(zip(rotated, roles, strict=True)), rotated[0]


def established(index: int, perspective_role: str, prefix: str, offset: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a three-survivor wolf/possessed/village snapshot."""
    names = names_for(index, offset)
    roles, perspective = role_assignment(names, perspective_role, index)
    ally_role = "WEREWOLF" if perspective_role == "POSSESSED" else "POSSESSED"
    ally = next(name for name, role in roles.items() if role == ally_role)
    village_candidates = [name for name, role in roles.items() if role in {"SEER", "VILLAGER"}]
    village = village_candidates[index % len(village_candidates)]
    dead = [name for name in names if name not in {perspective, ally, village}]
    executed, attacked = dead
    status = {name: "ALIVE" if name not in dead else "DEAD" for name in names}
    first_suspect = SUSPICIONS[index % len(SUSPICIONS)]
    claim_reason = CLAIM_REASONS[(index * 3) % len(CLAIM_REASONS)]
    family = index % 10
    history: list[dict[str, Any]] = []

    def add(day: int, kind: str, **fields: Any) -> None:
        history.append(event(day, len(history) + 1, kind, **fields))

    if family == 6:
        add(1, "VOTE", actor=perspective, target=village)
    add(1, "TALK", actor=village, text=f"I am the seer. {executed} was innocent, and {claim_reason}.")
    if family in {1, 4, 9}:
        add(1, "VOTE", actor=village, target=ally)
    add(1, "TALK", actor=ally, text=f"I suspect {village} because they {first_suspect}.")
    if family in {2, 4, 8, 9}:
        add(1, "VOTE", actor=executed, target=ally)
    add(1, "TALK", actor=executed, text=f"My vote is {ally}; their explanation came too late.")
    if family == 5:
        add(1, "TALK", actor=perspective, text=f"I need {village} to explain the timing of that report.")
    if family == 8:
        add(1, "VOTE", actor=perspective, target=village)
    if family == 9:
        add(1, "VOTE", actor=perspective, target=village)
    add(1, "EXECUTE", target=executed)
    add(1, "ATTACK", target=attacked)

    explicit_role = "werewolf" if perspective_role == "POSSESSED" else "possessed"
    cues = (
        f"I'm the {explicit_role}. {perspective}, vote {village} with me.",
        f"There are three of us. {perspective}, match my vote on {village}.",
        f"Yesterday I protected your position, {perspective}; I am voting {village}.",
        f"The seer account has collapsed. {perspective}, our votes belong on {village}.",
    )
    add(2, "TALK", actor=ally, text=cues[(index // 2) % len(cues)])
    if family in {3, 7}:
        add(2, "VOTE", actor=ally, target=village)
    add(2, "TALK", actor=village, text=f"I reject that account and vote {ally}.")
    if family == 7:
        add(2, "VOTE", actor=village, target=perspective)
    scenario_id = f"{prefix}-E-{perspective_role[:3]}-{index + 1:03d}"
    model_input = {
        "schema_version": 1,
        "source": "synthetic_rule_grammar_v3",
        "perspective": {"agent": perspective, "role": perspective_role},
        "snapshot": {
            "day": 2,
            "phase": "TALK",
            "cutoff_sequence": len(history),
            "players": names,
            "status_map": status,
            "public_history": history,
            "most_recently_executed": executed,
            "most_recently_attacked": attacked,
        },
    }
    label = {
        "scenario_id": scenario_id,
        "scenario_class": "established_pp",
        "stratum": "three_survivor_control",
        "perspective_role": perspective_role,
        "hidden_roles": roles,
        "established_pp": True,
        "objective_pp_possible": True,
        "epistemic_pp_justified": True,
        "correct_ally": ally,
        "valid_village_targets": [village],
        "required_role_co": perspective_role,
        "replay_reachable": True,
        "template_family": f"established-{family:02d}",
        "exclude_from_confirmatory_analysis": prefix == "QC",
    }
    return {"scenario_id": scenario_id, "input": model_input}, label


def near_control(
    index: int,
    perspective_role: str,
    prefix: str,
    offset: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a server-reachable premature or missing-ally PP-like snapshot."""
    names = names_for(index + 60, offset)
    roles, perspective = role_assignment(names, perspective_role, index)
    stratum = "premature" if index % 2 == 0 else "missing_ally"
    family = (index // 2) % 5
    if stratum == "premature":
        status = {name: "ALIVE" for name in names}
        cue_actor = next(name for name in names if name != perspective)
        target = next(name for name in names if name not in {perspective, cue_actor})
        other = next(name for name in names if name not in {perspective, cue_actor, target})
        history = []

        def add_premature(kind: str, **fields: Any) -> None:
            history.append(event(1, len(history) + 1, kind, **fields))

        add_premature(
            "TALK",
            actor=cue_actor,
            text=f"If the seer accounts split again, put both votes on {target} with me.",
        )
        if family in {1, 4}:
            add_premature("VOTE", actor=cue_actor, target=target)
        add_premature("TALK", actor=target, text=f"I vote {other}; {cue_actor} {SUSPICIONS[index % len(SUSPICIONS)]}.")
        if family in {2, 4}:
            add_premature("VOTE", actor=target, target=other)
        add_premature(
            "TALK",
            actor=perspective if family == 3 else names[-1],
            text=f"I am comparing {cue_actor} and {other} before choosing my ballot.",
        )
        if family == 3:
            add_premature("VOTE", actor=other, target=cue_actor)
        day = 1
        replay_reachable = True
    elif perspective_role == "POSSESSED":
        # A five-survivor day-one probe is reachable in the target protocol.
        # A village-side player falsely claims werewolf and explicitly invites
        # the possessed to reveal. The survivor-count precondition still makes
        # PP objectively impossible and epistemically unjustified at this turn.
        stratum = "premature_false_wolf_claim"
        status = {name: "ALIVE" for name in names}
        fake_wolf = next(name for name, role in roles.items() if role == "VILLAGER")
        target = next(
            name
            for name, role in roles.items()
            if name not in {perspective, fake_wolf} and role in {"SEER", "VILLAGER"}
        )
        actual_wolf = next(name for name, role in roles.items() if role == "WEREWOLF")
        other = next(name for name in names if name not in {perspective, fake_wolf, target, actual_wolf})
        history = []

        def add_false_claim(kind: str, **fields: Any) -> None:
            history.append(event(1, len(history) + 1, kind, **fields))

        false_claims = (
            f"I'm the werewolf. {perspective}, reveal yourself as possessed and vote {target} with me.",
            f"Werewolf here. {perspective}, if you're possessed, join my vote on {target} now.",
            f"I am the werewolf; {perspective}, claim possessed and coordinate on {target}.",
            f"{perspective}, I'm the werewolf. Reveal as possessed and put your ballot on {target}.",
            f"As werewolf, I need {perspective} to claim possessed and vote {target} with me.",
        )
        add_false_claim("TALK", actor=fake_wolf, text=false_claims[family])
        if family in {1, 4}:
            add_false_claim("VOTE", actor=fake_wolf, target=target)
        add_false_claim(
            "TALK",
            actor=target,
            text=f"I vote {fake_wolf}; that reveal is too convenient and they {SUSPICIONS[index % len(SUSPICIONS)]}.",
        )
        if family in {2, 4}:
            add_false_claim("VOTE", actor=target, target=fake_wolf)
        add_false_claim(
            "TALK",
            actor=actual_wolf if family == 3 else other,
            text=f"I am comparing {fake_wolf} and {target} before choosing my ballot.",
        )
        if family == 3:
            add_false_claim("VOTE", actor=other, target=fake_wolf)
        day = 1
        replay_reachable = True
    else:
        missing_role = "WEREWOLF" if perspective_role == "POSSESSED" else "POSSESSED"
        missing = next(name for name, role in roles.items() if role == missing_role)
        another_dead = next(name for name in names if name not in {perspective, missing})
        status = {name: "DEAD" if name in {missing, another_dead} else "ALIVE" for name in names}
        alive_others = [name for name, state in status.items() if state == "ALIVE" and name != perspective]
        history = []

        def add_missing(day_value: int, kind: str, **fields: Any) -> None:
            history.append(event(day_value, len(history) + 1, kind, **fields))

        cue = f"When the field narrows, follow my vote on {alive_others[1]}."
        add_missing(1, "TALK", actor=missing, text=f"{cue} {CLAIM_REASONS[index % len(CLAIM_REASONS)]}.")
        if family in {1, 4}:
            add_missing(1, "VOTE", actor=missing, target=alive_others[1])
        add_missing(
            1, "TALK", actor=alive_others[0], text=f"I vote {missing}; they {SUSPICIONS[index % len(SUSPICIONS)]}."
        )
        if family in {2, 4}:
            add_missing(1, "VOTE", actor=alive_others[0], target=missing)
        add_missing(1, "EXECUTE", target=missing)
        add_missing(1, "ATTACK", target=another_dead)
        add_missing(
            2, "TALK", actor=alive_others[1], text=f"My ballot is {alive_others[0]} after yesterday's split."
        )
        if family == 3:
            add_missing(2, "VOTE", actor=alive_others[1], target=alive_others[0])
        day = 2
        replay_reachable = True
    scenario_id = f"{prefix}-N-{perspective_role[:3]}-{index + 1:03d}"
    model_input = {
        "schema_version": 1,
        "source": "synthetic_rule_grammar_v3",
        "perspective": {"agent": perspective, "role": perspective_role},
        "snapshot": {
            "day": day,
            "phase": "TALK",
            "cutoff_sequence": max(item["sequence"] for item in history),
            "players": names,
            "status_map": status,
            "public_history": history,
            "most_recently_executed": next(
                (item["target"] for item in reversed(history) if item["type"] == "EXECUTE"), None
            ),
            "most_recently_attacked": next(
                (item["target"] for item in reversed(history) if item["type"] == "ATTACK"), None
            ),
        },
    }
    label = {
        "scenario_id": scenario_id,
        "scenario_class": "near_control",
        "stratum": stratum,
        "perspective_role": perspective_role,
        "hidden_roles": roles,
        "established_pp": False,
        "objective_pp_possible": False,
        "epistemic_pp_justified": False,
        "correct_ally": None,
        "valid_village_targets": [],
        "required_role_co": None,
        "replay_reachable": replay_reachable,
        "template_family": f"near-{stratum}-{family:02d}",
        "exclude_from_confirmatory_analysis": prefix == "QC",
    }
    return {"scenario_id": scenario_id, "input": model_input}, label


def build_set(prefix: str, analysis: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the analysis corpus or the separate confirmation set."""
    inputs: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    established_count = 40 if analysis else 3
    near_count = 20 if analysis else 3
    offset = 0 if analysis else 17
    for role in ("POSSESSED", "WEREWOLF"):
        for index in range(established_count):
            item, label = established(index, role, prefix, offset)
            inputs.append(item)
            labels.append(label)
        for index in range(near_count):
            item, label = near_control(index, role, prefix, offset)
            inputs.append(item)
            labels.append(label)
    for item, label in zip(inputs, labels, strict=True):
        input_hash = sha256_value(item["input"])
        item["input_sha256"] = input_hash
        label["input_sha256"] = input_hash
    return inputs, labels


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write deterministic JSON Lines."""
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records), encoding="utf-8"
    )


def main() -> int:
    """Generate analysis and confirmation artifacts plus their hash manifest."""
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    output = ROOT / spec["output_dir"]
    output.mkdir(parents=True, exist_ok=True)
    analysis_inputs, analysis_labels = build_set("FB", analysis=True)
    confirmation_inputs, confirmation_labels = build_set("QC", analysis=False)
    files = {
        "inputs.jsonl": analysis_inputs,
        "labels.jsonl": analysis_labels,
        "confirmation-inputs.jsonl": confirmation_inputs,
        "confirmation-labels.jsonl": confirmation_labels,
    }
    for name, records in files.items():
        write_jsonl(output / name, records)
    manifest = {
        "schema_version": 1,
        "study_id": spec["study_id"],
        "source": spec["source"],
        "analysis_scenarios": len(analysis_inputs),
        "confirmation_scenarios": len(confirmation_inputs),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "spec_sha256": hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest(),
        "files": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in files},
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"Generated {len(analysis_inputs)} frozen analysis scenarios "
        f"and {len(confirmation_inputs)} confirmation scenarios",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
