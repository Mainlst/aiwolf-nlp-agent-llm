#!/usr/bin/env python3
"""Create the preregistered 60-unit blinded human-audit sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS = ROOT / "experiments" / "pp" / "fixed_boards_v2" / "generated"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("judge_inputs", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--sample-size", type=int, default=60)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_dir}")

    blinds = {row["blind_id"]: row for row in read_jsonl(args.judge_inputs / "blind-inputs.jsonl")}
    private = read_jsonl(args.judge_inputs / "private-map.jsonl")
    labels = {
        row["scenario_id"]: row for row in read_jsonl(args.corpus_dir.resolve() / "labels.jsonl")
    }
    strata: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in private:
        label = labels[row["scenario_id"]]
        key = (row["condition"], label["perspective_role"], label["scenario_class"])
        strata[key].append(row)
    keys = sorted(strata)
    if args.sample_size < len(keys):
        raise ValueError("sample size is smaller than the number of registered strata")
    rng = random.Random(args.seed)
    selected: list[dict[str, Any]] = []
    base, remainder = divmod(args.sample_size, len(keys))
    for index, key in enumerate(keys):
        rows = sorted(strata[key], key=lambda row: row["blind_id"])
        rng.shuffle(rows)
        quota = base + (index < remainder)
        if len(rows) < quota:
            raise ValueError(f"insufficient units in stratum {key}")
        selected.extend(rows[:quota])
    rng.shuffle(selected)

    audit_inputs = []
    audit_private = []
    for ordinal, row in enumerate(selected, 1):
        audit_id = f"HA-{ordinal:03d}"
        blind = dict(blinds[row["blind_id"]])
        blind["audit_id"] = audit_id
        blind.pop("blind_id")
        blind["human_labels"] = {
            "pp_recognized": None,
            "ally_recognized": None,
            "vote_coordination": None,
        }
        blind["adjudication_note"] = ""
        audit_inputs.append(blind)
        label = labels[row["scenario_id"]]
        audit_private.append({
            "audit_id": audit_id,
            "blind_id": row["blind_id"],
            "condition": row["condition"],
            "perspective_role": label["perspective_role"],
            "scenario_class": label["scenario_class"],
        })

    args.output_dir.mkdir(parents=True)
    write_jsonl(args.output_dir / "human-audit-inputs.jsonl", audit_inputs)
    write_jsonl(args.output_dir / "human-audit-private-map.jsonl", audit_private)
    manifest = {
        "schema_version": 1,
        "seed": args.seed,
        "sample_size": len(selected),
        "stratification": ["condition", "perspective_role", "scenario_class"],
        "condition_blinded_in_human_file": True,
        "source_blind_inputs_sha256": hashlib.sha256(
            (args.judge_inputs / "blind-inputs.jsonl").read_bytes()
        ).hexdigest(),
        "files": {
            name: hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
            for name in ("human-audit-inputs.jsonl", "human-audit-private-map.jsonl")
        },
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"sampled={len(selected)} strata={len(keys)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
