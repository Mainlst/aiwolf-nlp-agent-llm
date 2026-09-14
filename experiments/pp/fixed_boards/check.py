#!/usr/bin/env python3
"""Fail-closed audit of the frozen synthetic fixed-board corpus."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

try:
    from .generate import canonical
except ImportError:  # Direct script execution.
    from generate import canonical

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSON-object records."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def nested_keys(value: Any) -> set[str]:
    """Collect keys recursively for future-information leakage checks."""
    if isinstance(value, dict):
        return set(value) | set().union(*(nested_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value), set())
    return set()


def audit_pair(item: dict[str, Any], label: dict[str, Any], forbidden: set[str]) -> list[str]:
    """Return all validation errors for one input/label pair."""
    errors: list[str] = []
    scenario_id = item.get("scenario_id")
    if scenario_id != label.get("scenario_id"):
        errors.append(f"{scenario_id}: input/label ID mismatch")
    expected_hash = hashlib.sha256(canonical(item.get("input"))).hexdigest()
    if item.get("input_sha256") != expected_hash or label.get("input_sha256") != expected_hash:
        errors.append(f"{scenario_id}: input hash mismatch")
    model_input = item.get("input", {})
    leaked = forbidden & nested_keys(model_input)
    if leaked:
        errors.append(f"{scenario_id}: future/label keys leaked into input: {sorted(leaked)}")
    snapshot = model_input.get("snapshot", {})
    status = snapshot.get("status_map", {})
    history = snapshot.get("public_history", [])
    cutoff = snapshot.get("cutoff_sequence")
    if any(
        event.get("sequence", cutoff + 1) > cutoff
        or event.get("day", snapshot.get("day", -1) + 1) > snapshot.get("day")
        for event in history
    ):
        errors.append(f"{scenario_id}: event after snapshot cutoff")
    event_ids = [event.get("event_id") for event in history]
    if len(event_ids) != len(set(event_ids)):
        errors.append(f"{scenario_id}: duplicate public event IDs")
    roles = label.get("hidden_roles", {})
    if set(roles) != set(status):
        errors.append(f"{scenario_id}: role/status player mismatch")
        return errors
    if Counter(roles.values()) != {"WEREWOLF": 1, "POSSESSED": 1, "SEER": 1, "VILLAGER": 2}:
        errors.append(f"{scenario_id}: five-player role composition mismatch")
    perspective = model_input.get("perspective", {})
    if roles.get(perspective.get("agent")) != perspective.get("role"):
        errors.append(f"{scenario_id}: perspective role label mismatch")
    sequences = [event.get("sequence") for event in history]
    if sequences != list(range(1, len(history) + 1)):
        errors.append(f"{scenario_id}: non-contiguous event sequence")
    dead: set[str] = set()
    wolf = next((name for name, role in roles.items() if role == "WEREWOLF"), None)
    wolf_dead = False
    for public_event in history:
        kind = public_event.get("type")
        actor = public_event.get("actor")
        target = public_event.get("target")
        if actor in dead:
            errors.append(f"{scenario_id}: dead actor {actor} performs {kind}")
        if wolf_dead:
            errors.append(f"{scenario_id}: event occurs after werewolf death")
        if kind in {"EXECUTE", "ATTACK"}:
            if target not in status:
                errors.append(f"{scenario_id}: death target is not a player")
            elif target in dead:
                errors.append(f"{scenario_id}: player {target} dies twice")
            dead.add(target)
            wolf_dead = target == wolf
    status_dead = {name for name, state in status.items() if state == "DEAD"}
    if dead != status_dead:
        errors.append(f"{scenario_id}: public deaths do not match status map")
    alive = [name for name, state in status.items() if state == "ALIVE"]
    alive_roles = [roles[name] for name in alive]
    server_terminal = (
        alive_roles.count("WEREWOLF") == 0
        or alive_roles.count("WEREWOLF") >= len(alive_roles) - alive_roles.count("WEREWOLF")
    )
    if server_terminal:
        errors.append(f"{scenario_id}: snapshot is post-terminal")
    if label.get("replay_reachable") is not True:
        errors.append(f"{scenario_id}: corpus contains a replay-unreachable snapshot")
    established = label.get("scenario_class") == "established_pp"
    mechanically_established = (
        len(alive) == 3
        and alive_roles.count("WEREWOLF") == 1
        and alive_roles.count("POSSESSED") == 1
        and sum(role in {"SEER", "VILLAGER"} for role in alive_roles) == 1
    )
    if established != mechanically_established or bool(label.get("established_pp")) != mechanically_established:
        errors.append(f"{scenario_id}: established-PP ground truth mismatch")
    if bool(label.get("objective_pp_possible")) != mechanically_established:
        errors.append(f"{scenario_id}: objective PP possibility mismatch")
    if bool(label.get("epistemic_pp_justified")) != established:
        errors.append(f"{scenario_id}: epistemic PP justification mismatch")
    if established:
        if len(alive) != 3 or perspective.get("role") not in {"WEREWOLF", "POSSESSED"}:
            errors.append(f"{scenario_id}: public snapshot is not PP-eligible from the perspective")
        perspective_role = perspective["role"]
        ally_role = "WEREWOLF" if perspective_role == "POSSESSED" else "POSSESSED"
        correct_ally = next((name for name in alive if roles[name] == ally_role), None)
        village = [name for name in alive if roles[name] in {"SEER", "VILLAGER"}]
        if label.get("correct_ally") != correct_ally or label.get("valid_village_targets") != village:
            errors.append(f"{scenario_id}: ally or village target label mismatch")
    else:
        if label.get("correct_ally") is not None or label.get("valid_village_targets"):
            errors.append(f"{scenario_id}: near-control carries PP target labels")
        stratum = label.get("stratum")
        if stratum in {"premature", "premature_false_wolf_claim"} and len(alive) <= 3:
            errors.append(f"{scenario_id}: premature control has no more than three survivors")
        if stratum == "missing_ally" and (len(alive) != 3 or mechanically_established):
            errors.append(f"{scenario_id}: missing-ally control is malformed")
    public_text = " ".join(str(event.get("text", "")) for event in history).casefold()
    cue_markers = ("coordinate", "unite", "possessed", "seer", "vote")
    if not any(marker in public_text for marker in cue_markers):
        errors.append(f"{scenario_id}: no public PP-like or endgame cue")
    answer_revealing_phrases = (
        "coordination is premature",
        "nobody should reveal",
        "should not reveal",
        "do not reveal",
        "cue was unreliable",
        "coordination cue was unreliable",
        "this is not pp",
        "pp is not established",
    )
    if not established and any(phrase in public_text for phrase in answer_revealing_phrases):
        errors.append(f"{scenario_id}: near-control text reveals the negative label")
    return errors


def main() -> int:
    """Audit counts, provenance, hashes, leakage, labels, and duplicates."""
    spec = yaml.safe_load((HERE / "spec.yml").read_text(encoding="utf-8"))
    output = ROOT / spec["output_dir"]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    if spec["source"]["tournament_logs_used"] is not False:
        errors.append("source provenance does not prohibit tournament logs")
    expected_generator_hash = hashlib.sha256((HERE / "generate.py").read_bytes()).hexdigest()
    expected_spec_hash = hashlib.sha256((HERE / "spec.yml").read_bytes()).hexdigest()
    if manifest.get("generator_sha256") != expected_generator_hash or manifest.get("spec_sha256") != expected_spec_hash:
        errors.append("manifest generator/spec hash mismatch")
    for name, expected in manifest.get("files", {}).items():
        actual = hashlib.sha256((output / name).read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"manifest file hash mismatch: {name}")
    analysis_inputs = read_jsonl(output / "inputs.jsonl")
    analysis_labels = read_jsonl(output / "labels.jsonl")
    confirmation_inputs = read_jsonl(output / "confirmation-inputs.jsonl")
    confirmation_labels = read_jsonl(output / "confirmation-labels.jsonl")
    if len(analysis_inputs) != len(analysis_labels) or len(confirmation_inputs) != len(confirmation_labels):
        errors.append("input/label record count mismatch")
    forbidden = set(spec["future_information_forbidden_keys"])
    for item, label in zip(
        [*analysis_inputs, *confirmation_inputs], [*analysis_labels, *confirmation_labels], strict=True
    ):
        errors.extend(audit_pair(item, label, forbidden))
    analysis_counts = Counter((label["scenario_class"], label["perspective_role"]) for label in analysis_labels)
    expected_analysis = {
        (scenario_class, role): count
        for scenario_class, role_counts in spec["analysis_counts"].items()
        for role, count in role_counts.items()
    }
    if dict(analysis_counts) != expected_analysis:
        errors.append(f"analysis strata mismatch: {dict(analysis_counts)}")
    confirmation_counts = Counter((label["scenario_class"], label["perspective_role"]) for label in confirmation_labels)
    expected_confirmation = {
        (scenario_class, role): count
        for scenario_class, role_counts in spec["confirmation_counts"].items()
        for role, count in role_counts.items()
    }
    if dict(confirmation_counts) != expected_confirmation:
        errors.append(f"confirmation strata mismatch: {dict(confirmation_counts)}")
    near_strata_by_role = Counter(
        (label["perspective_role"], label["stratum"])
        for label in analysis_labels
        if label["scenario_class"] == "near_control"
    )
    expected_near = {
        (role, stratum): count
        for role, strata in spec["near_control_strata"].items()
        for stratum, count in strata.items()
    }
    if dict(near_strata_by_role) != expected_near:
        errors.append(f"near-control role/stratum mismatch: {dict(near_strata_by_role)}")
    family_counts = Counter(
        (label["scenario_class"], label["perspective_role"], label["stratum"], label.get("template_family"))
        for label in analysis_labels
    )
    maximum_family_size = int(spec["diversity"]["maximum_analysis_records_per_family"])
    if any(family is None or count > maximum_family_size for (*_, family), count in family_counts.items()):
        errors.append(f"template family missing or overrepresented: {dict(family_counts)}")
    input_by_id = {item["scenario_id"]: item for item in analysis_inputs}
    for role in ("POSSESSED", "WEREWOLF"):
        established_families = {
            label["template_family"]
            for label in analysis_labels
            if label["scenario_class"] == "established_pp" and label["perspective_role"] == role
        }
        if len(established_families) < int(spec["diversity"]["established_families_per_role"]):
            errors.append(f"{role}: insufficient established-PP template diversity")
        established_topologies = {
            tuple(
                (event["day"], event["type"])
                for event in input_by_id[label["scenario_id"]]["input"]["snapshot"]["public_history"]
            )
            for label in analysis_labels
            if label["scenario_class"] == "established_pp" and label["perspective_role"] == role
        }
        if len(established_topologies) < int(spec["diversity"]["established_families_per_role"]):
            errors.append(f"{role}: insufficient established-PP event-topology diversity")
        for stratum in spec["near_control_strata"][role]:
            near_families = {
                label["template_family"]
                for label in analysis_labels
                if label["scenario_class"] == "near_control"
                and label["perspective_role"] == role
                and label["stratum"] == stratum
            }
            if len(near_families) < int(spec["diversity"]["near_families_per_role_stratum"]):
                errors.append(f"{role}/{stratum}: insufficient near-control template diversity")
            near_topologies = {
                tuple(
                    (event["day"], event["type"])
                    for event in input_by_id[label["scenario_id"]]["input"]["snapshot"]["public_history"]
                )
                for label in analysis_labels
                if label["scenario_class"] == "near_control"
                and label["perspective_role"] == role
                and label["stratum"] == stratum
            }
            if len(near_topologies) < int(spec["diversity"]["near_families_per_role_stratum"]):
                errors.append(f"{role}/{stratum}: insufficient near-control event-topology diversity")
    analysis_hashes = [item["input_sha256"] for item in analysis_inputs]
    confirmation_hashes = [item["input_sha256"] for item in confirmation_inputs]
    if len(set(analysis_hashes)) != len(analysis_hashes):
        errors.append("duplicate model input in analysis corpus")
    if len(set(confirmation_hashes)) != len(confirmation_hashes):
        errors.append("duplicate model input in confirmation set")
    overlap = set(analysis_hashes) & set(confirmation_hashes)
    if overlap:
        errors.append(f"confirmation set overlaps analysis corpus: {len(overlap)} inputs")
    if not all(label.get("exclude_from_confirmatory_analysis") is False for label in analysis_labels):
        errors.append("analysis corpus exclusion flag mismatch")
    if not all(label.get("exclude_from_confirmatory_analysis") is True for label in confirmation_labels):
        errors.append("confirmation exclusion flag mismatch")
    if errors:
        raise SystemExit("\n".join(errors))
    near_strata = Counter(label["stratum"] for label in analysis_labels if label["scenario_class"] == "near_control")
    print(
        "OK: synthetic source; 120 unique frozen inputs; "
        f"established POSSESSED/WEREWOLF=40/40; near=20/20; near strata={dict(near_strata)}; "
        f"template families={len(family_counts)}; chronological histories; no answer-revealing control text; "
        "no future-label leakage; 12 disjoint confirmation inputs",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
