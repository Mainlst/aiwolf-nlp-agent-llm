#!/usr/bin/env python3
"""Generate the frozen P+ and P-PP configs without calling an API."""

# ruff: noqa: EM101, EM102, T201, TRY003, TRY004

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = ROOT / "experiments" / "pp" / "spec.yml"


def load_mapping(path: Path) -> dict[str, Any]:
    """Load a YAML mapping."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected YAML mapping: {path}")
    return value


def load_spec(path: Path) -> tuple[dict[str, Any], list[Path]]:
    """Load a standalone spec or resolve a small overlay over a frozen parent spec."""
    raw = load_mapping(path)
    parent_value = raw.get("extends")
    if not parent_value:
        return raw, [path.resolve()]
    parent_path = (ROOT / str(parent_value)).resolve()
    resolved = deepcopy(load_mapping(parent_path))
    for key, value in raw.items():
        if key not in {"extends", "fragment_text_overrides"}:
            resolved[key] = deepcopy(value)
    overrides = raw.get("fragment_text_overrides", {})
    if not isinstance(overrides, dict):
        raise TypeError("fragment_text_overrides must be a mapping")
    fragments = {str(fragment["id"]): fragment for fragment in resolved["static_fragments"]}
    unknown = sorted(set(overrides) - set(fragments))
    if unknown:
        raise KeyError(f"Unknown fragment_text_overrides IDs: {unknown}")
    for fragment_id, replacement in overrides.items():
        fragments[str(fragment_id)]["text"] = str(replacement)
    return resolved, [parent_path, path.resolve()]


def nested_text(config: dict[str, Any], dotted_path: str) -> str:
    """Return a string at a dotted mapping path."""
    value: Any = config
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"Missing config path: {dotted_path}")
        value = value[part]
    if not isinstance(value, str):
        raise TypeError(f"Config path is not text: {dotted_path}")
    return value


def set_nested_text(config: dict[str, Any], dotted_path: str, text: str) -> None:
    """Set a string at a dotted mapping path."""
    value: dict[str, Any] = config
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        child = value.get(part)
        if not isinstance(child, dict):
            raise KeyError(f"Missing config path: {dotted_path}")
        value = child
    value[parts[-1]] = text


def remove_exact_line(text: str, fragment: str, fragment_id: str) -> str:
    """Remove exactly one registered line and preserve all other lines."""
    lines = text.splitlines()
    matches = [index for index, line in enumerate(lines) if line == fragment]
    if len(matches) != 1:
        raise ValueError(f"Fragment {fragment_id} matched {len(matches)} lines; expected exactly one")
    del lines[matches[0]]
    return "\n".join(lines)


def replace_exact_line(text: str, old: str, new: str, replacement_id: str) -> str:
    """Replace exactly one line and fail closed when the frozen base has drifted."""
    lines = text.splitlines()
    matches = [index for index, line in enumerate(lines) if line == old]
    if len(matches) != 1:
        raise ValueError(f"Replacement {replacement_id} matched {len(matches)} lines; expected exactly one")
    lines[matches[0]] = new
    return "\n".join(lines)


def generate(spec: dict[str, Any], base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return P+ and P-PP configs generated from one base mapping."""
    corrected_base = deepcopy(base)
    for replacement in spec.get("shared_replacements", []):
        dotted_path = str(replacement["path"])
        current = nested_text(corrected_base, dotted_path)
        updated = replace_exact_line(
            current,
            str(replacement["old"]),
            str(replacement["new"]),
            str(replacement["id"]),
        )
        set_nested_text(corrected_base, dotted_path, updated)
    outputs: dict[str, dict[str, Any]] = {}
    for condition, condition_spec in spec["conditions"].items():
        config = deepcopy(corrected_base)
        enabled = bool(condition_spec["pp_instruction_enabled"])
        if not enabled:
            for fragment in spec["static_fragments"]:
                dotted_path = str(fragment["path"])
                current = nested_text(config, dotted_path)
                updated = remove_exact_line(current, str(fragment["text"]), str(fragment["id"]))
                set_nested_text(config, dotted_path, updated)
        experiment = config.setdefault("experiment", {})
        if not isinstance(experiment, dict):
            raise TypeError("config.experiment must be a mapping")
        experiment.update(
            {
                "study_id": spec["study_id"],
                "condition": condition,
                "label": condition_spec["label"],
                "pp_instruction_enabled": enabled,
                "dynamic_context_enabled": True,
                "role_strategy_enabled": True,
                "intervention_version": spec.get("intervention_version", "v1-tournament"),
            },
        )
        outputs[condition] = config
    return outputs


def sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    """Write generated configs and their provenance manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments" / "pp" / "generated")
    args = parser.parse_args()

    spec_path = args.spec.resolve()
    spec, spec_dependencies = load_spec(spec_path)
    base_path = (ROOT / str(spec["base_config"])).resolve()
    evaluation_spec_path = (ROOT / str(spec.get("evaluation_spec", "experiments/pp/evaluation-spec.yml"))).resolve()
    runtime_source_path = (ROOT / str(spec.get("runtime_source", "src/agent/agent.py"))).resolve()
    base = load_mapping(base_path)
    outputs = generate(spec, base)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "study_id": spec["study_id"],
        "intervention_version": spec.get("intervention_version", "v1-tournament"),
        "spec": str(spec_path.relative_to(ROOT)),
        "spec_sha256": sha256_bytes(spec_path.read_bytes()),
        "spec_dependencies": {
            str(path.relative_to(ROOT)): sha256_bytes(path.read_bytes()) for path in spec_dependencies
        },
        "base_config": str(base_path.relative_to(ROOT)),
        "base_config_sha256": sha256_bytes(base_path.read_bytes()),
        "evaluation_spec": str(evaluation_spec_path.relative_to(ROOT)),
        "evaluation_spec_sha256": sha256_bytes(evaluation_spec_path.read_bytes()),
        "runtime_source": str(runtime_source_path.relative_to(ROOT)),
        "runtime_source_sha256": sha256_bytes(runtime_source_path.read_bytes()),
        "configs": {},
    }
    for condition, config in outputs.items():
        payload = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode()
        filename = f"{condition}.yml"
        (args.output_dir / filename).write_bytes(payload)
        manifest["configs"][condition] = {"file": filename, "sha256": sha256_bytes(payload)}
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {', '.join(outputs)} in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
