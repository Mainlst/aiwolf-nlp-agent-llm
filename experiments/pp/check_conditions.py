#!/usr/bin/env python3
"""Fail-closed audit of generated P+ and P-PP prompts; never calls an API."""

# ruff: noqa: C901, EM101, EM102, T201, TRY003

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from generate_conditions import (
    DEFAULT_SPEC,
    ROOT,
    generate,
    load_mapping,
    load_spec,
    nested_text,
    remove_exact_line,
    set_nested_text,
)
from jinja2 import Template


def without_condition_metadata(config: dict[str, Any]) -> dict[str, Any]:
    """Return a config with condition-identifying experiment values removed."""
    result = deepcopy(config)
    experiment = result.get("experiment", {})
    if isinstance(experiment, dict):
        for key in ("condition", "label", "pp_instruction_enabled"):
            experiment.pop(key, None)
    return result


def expected_p_minus(p_plus: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Remove the registered static fragments from P+ for exact comparison."""
    expected = deepcopy(p_plus)
    for fragment in spec["static_fragments"]:
        path = str(fragment["path"])
        updated = remove_exact_line(
            nested_text(expected, path),
            str(fragment["text"]),
            str(fragment["id"]),
        )
        set_nested_text(expected, path, updated)
    return expected


def render_prompt(config: dict[str, Any], spec: dict[str, Any], request: str, role: str) -> str:
    """Render the active compact template with a fixed synthetic context."""
    prompt_config = config["prompt"]
    template_text = prompt_config.get(f"{request}_compact", prompt_config[request])
    info = SimpleNamespace(
        day=2,
        agent="Alice",
        divine_result=None,
        status_map={"Alice": "ALIVE", "Bob": "ALIVE", "Carol": "ALIVE"},
    )
    rendered = Template(template_text).render(
        info=info,
        role=role,
        strategy=config["strategy"][role],
        game_mastery=prompt_config.get("game_mastery_compact", prompt_config["game_mastery"]),
        talk_history=[],
        current_day_own_talk_context="Alice: I suspect Carol.",
    )
    dynamic = [
        "[Endgame notes]",
        "Few players remain, so prioritize a vote target and reason.",
    ]
    if config["experiment"]["pp_instruction_enabled"]:
        dynamic.extend(str(item["marker"]) for item in spec["runtime_fragments"])
    return f"{rendered.strip()}\n\n" + "\n".join(dynamic)


def audit(spec: dict[str, Any], outputs: dict[str, dict[str, Any]], audit_dir: Path | None = None) -> dict[str, Any]:
    """Audit static config equality, forbidden residue, and expanded prompts."""
    ids = [str(item["id"]) for key in ("static_fragments", "runtime_fragments") for item in spec[key]]
    if len(ids) != len(set(ids)):
        raise ValueError("Every static/runtime fragment ID must be unique")

    p_plus = outputs["P+"]
    p_minus = outputs["P-PP"]
    expected = expected_p_minus(p_plus, spec)
    if without_condition_metadata(expected) != without_condition_metadata(p_minus):
        raise ValueError("P+ and P-PP differ outside the registered PP fragments and condition metadata")

    instruction_surface = {
        "strategy": p_minus.get("strategy", {}),
        "prompt": p_minus.get("prompt", {}),
    }
    serialized_minus = yaml.safe_dump(instruction_surface, allow_unicode=True, sort_keys=False)
    residue = [pattern for pattern in spec["forbidden_in_p_minus"] if re.search(str(pattern), serialized_minus)]
    if residue:
        raise ValueError(f"P-PP contains forbidden PP residue: {residue}")

    both_serialized = {
        condition: yaml.safe_dump(
            {"strategy": config.get("strategy", {}), "prompt": config.get("prompt", {})},
            allow_unicode=True,
            sort_keys=False,
        )
        for condition, config in outputs.items()
    }
    for condition, text in both_serialized.items():
        conflicts = [
            pattern for pattern in spec.get("forbidden_conflicts_in_both_conditions", [])
            if re.search(str(pattern), text)
        ]
        if conflicts:
            raise ValueError(f"{condition} contains a registered instruction conflict: {conflicts}")
    missing_required = [
        pattern for pattern in spec.get("required_in_p_plus", [])
        if not re.search(str(pattern), both_serialized["P+"])
    ]
    if missing_required:
        raise ValueError(f"P+ lacks required conflict-resolved instructions: {missing_required}")

    expanded: dict[str, str] = {}
    for condition, config in outputs.items():
        for role in ("POSSESSED", "WEREWOLF"):
            for request in ("talk", "vote"):
                key = f"{condition}-{role}-{request}"
                expanded[key] = render_prompt(config, spec, request, role)
    for key, prompt in expanded.items():
        if key.startswith("P-PP-"):
            matched = [pattern for pattern in spec["forbidden_in_p_minus"] if re.search(str(pattern), prompt)]
            if matched:
                raise ValueError(f"Expanded prompt {key} contains PP residue: {matched}")

    plus_markers = [str(item["marker"]) for item in spec["runtime_fragments"]]
    if not all(
        any(marker in prompt for key, prompt in expanded.items() if key.startswith("P+-")) for marker in plus_markers
    ):
        raise ValueError("One or more registered runtime markers are absent from expanded P+ prompts")
    if audit_dir is not None:
        audit_dir.mkdir(parents=True, exist_ok=True)
        for key, prompt in expanded.items():
            (audit_dir / f"{key}.txt").write_text(prompt + "\n", encoding="utf-8")
        report = {
            "status": "pass",
            "static_fragment_count": len(spec["static_fragments"]),
            "runtime_fragment_count": len(spec["runtime_fragments"]),
            "expanded_prompt_count": len(expanded),
            "conditions": sorted(outputs),
        }
        (audit_dir / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {
        "static": len(spec["static_fragments"]),
        "runtime": len(spec["runtime_fragments"]),
        "expanded": len(expanded),
    }


def main() -> int:
    """Generate in memory, audit, and optionally save expanded prompt evidence."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--audit-dir", type=Path, default=ROOT / "experiments" / "pp" / "generated" / "audit")
    parser.add_argument("--generated-dir", type=Path)
    args = parser.parse_args()
    spec, _ = load_spec(args.spec.resolve())
    base = load_mapping((ROOT / str(spec["base_config"])).resolve())
    outputs = generate(spec, base)
    result = audit(spec, outputs, args.audit_dir)
    if args.generated_dir is not None:
        for condition, expected in outputs.items():
            saved = load_mapping(args.generated_dir / f"{condition}.yml")
            if saved != expected:
                raise ValueError(f"Saved generated config differs from in-memory generation: {condition}")
    print(
        "OK: P+/P-PP differ only by "
        f"{result['static']} static and {result['runtime']} runtime PP fragments; "
        f"audited {result['expanded']} expanded prompts",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
