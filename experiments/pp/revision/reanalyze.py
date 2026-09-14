"""Reanalyze archived PP outputs with corrected extraction and immutable provenance.

No credentials, model calls, game processes, or writes into source runs are used.
An existing completed output is refused. Historical execution verification remains
strict; this separate analysis entry checks archived source rather than live code.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType

from experiments.pp.fixed_boards import score_analysis
from experiments.pp.fixed_boards.score_confirmation import named_target, role_co
from experiments.pp.fixed_boards.scoring_rules import SCORING_RULES_VERSION, vote_target_details
from experiments.pp.revision.analyze import analyze_a, analyze_b
from experiments.pp.revision.common import ROOT, digest, jsonl, read, write, verify_imported
from experiments.pp.revision.normal import row_result
from experiments.pp.v2.fixed_board_cue_audit import create_artifact
from experiments.pp.v2.normal.analyze_results import final_role_map, proportion

RUNS = ROOT / "experiments/runs"
DEFAULT_A = RUNS / "pp-revision-A-20260909-resume8-v1"
DEFAULT_B = RUNS / "pp-revision-B-20260909-resume8-v1"
DEFAULT_FIXED = RUNS / "pp-v2-fixed-analysis-reachable-v4-luna-20260816-v1"
DEFAULT_NORMAL = RUNS / "pp-v2-normal-400-clean-20260815-v1"
ORIGINAL_JUDGE = RUNS / "pp-v2-fixed-judge-reachable-v4-sol-20260816-v1"
OLD_NORMAL_ANALYSIS = ROOT / "experiments/pp/v2/normal/results/pp-v2-normal-400-clean-20260815-v1/analysis.json"
PROTOCOL = Path(__file__).with_name("scoring-correction-protocol.md")


def verify_hashes(base, expected):
    for relative, expected_hash in expected.items():
        if digest(base / relative) != expected_hash:
            raise ValueError(f"Archived artifact changed: {base / relative}")


def analysis_sources():
    """Inventory actual code, pruning large archived runs before descending."""
    paths = []
    for top in (ROOT / "src", ROOT / "experiments", ROOT / "tests"):
        for folder, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in ("runs", "__pycache__"))
            paths += [Path(folder) / name for name in sorted(files) if name.endswith(".py")]
    return paths + [ROOT / "pyproject.toml", ROOT / "uv.lock", PROTOCOL]


def verify_archived_revision(run):
    """Check saved source and the entire frozen evidence without runtime coupling."""
    manifest, frozen = read(run / "manifest.json"), read(run / "frozen.json")
    verify_hashes(run, manifest["files"])
    verify_hashes(run / "source", manifest["source_hashes"])
    if frozen["manifest_sha256"] != digest(run / "manifest.json"):
        raise ValueError("Frozen manifest changed")
    observed = {str(p.relative_to(run)) for folder in ("attempts", "units", "groups", "judge")
                for p in (run / folder).rglob("*") if p.is_file()}
    if observed != set(frozen["evidence"]):
        raise ValueError("Frozen evidence set changed")
    verify_hashes(run, frozen["evidence"])
    if read(run / "state.json") != frozen["state"] or not frozen["state"].get("schedule_finished"):
        raise ValueError("Frozen operational state is incomplete or changed")
    verify_imported(run)
    return manifest, frozen


def checkpoint_inputs(roots):
    """Verify original samples against their already archived checkpoint hashes."""
    folder = RUNS / "checkpoint-pp-revision-AB-20260909-v1"
    expected_manifest_hash = read(ROOT / "experiments/pp/revision/checkpoints/20260909-AB/artifact-summary.json")["manifest"]["sha256"]
    manifest_path = folder / "artifact-manifest.jsonl"
    if digest(manifest_path) != expected_manifest_hash:
        raise ValueError("Checkpoint content manifest changed")
    prefixes = [str(p.relative_to(ROOT)) + "/" for p in roots]
    expected = {r["path"]: r["sha256"] for r in jsonl(manifest_path)
                if r["type"] == "file" and any(r["path"].startswith(s) for s in prefixes)}
    # The archive excluded derived interpreter caches. They are neither source
    # nor experimental evidence and can predate this reanalysis.
    actual = {str(p.relative_to(ROOT)) for root in roots for p in root.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    if actual != set(expected):
        raise ValueError(f"Original sample files changed: {sorted(actual ^ set(expected))[:10]}")
    verify_hashes(ROOT, expected)
    return expected


def original_fixed(run, output):
    manifest = read(run / "manifest.json")
    corpus = ROOT / manifest["corpus_dir"]
    for name in ("inputs.jsonl", "labels.jsonl"):
        if digest(corpus / name) != manifest["files"][name]:
            raise ValueError("Fixed-board corpus changed")
    items = {r["scenario_id"]: r for r in jsonl(corpus / "inputs.jsonl")}
    labels = {r["scenario_id"]: r for r in jsonl(corpus / "labels.jsonl")}
    private = jsonl(run / "judge-inputs-v1/private-map.jsonl")
    semantic_rows = jsonl(run / "judge-inputs-v1/semantic-scores.jsonl")
    semantic = {r["blind_id"]: r for r in semantic_rows}
    if len(private) != 480 or len(semantic) != 480 or {r["blind_id"] for r in private} != set(semantic):
        raise ValueError("Incomplete fixed-board judgment join")
    units = {p.stem: read(p) for p in (run / "units").glob("*.json")}
    if len(units) != 480 or set(units) != {r["unit_id"] for r in private}:
        raise ValueError("Incomplete original fixed sample")
    scores = []
    for link in private:
        unit, judgments = units[link["unit_id"]], semantic[link["blind_id"]]
        if any(unit[k] != link[k] for k in ("game_id", "input_sha256")):
            raise ValueError("Original judgment identity mismatch")
        if unit["input_sha256"] != items[unit["scenario_id"]]["input_sha256"]:
            raise ValueError("Original unit input mismatch")
        for field in score_analysis.SEMANTIC_FIELDS:
            votes = judgments["judge_votes"][field]
            if len(votes) != 3 or (sum(votes) >= 2) != judgments["labels"][field]:
                raise ValueError("Original semantic majority mismatch")
        scores.append(score_analysis.score_unit(unit, items[unit["scenario_id"]], labels[unit["scenario_id"]], judgments["labels"]))
    scores.sort(key=lambda r: (r["scenario_id"], r["condition"], r["generation_repetition"]))
    scenarios = score_analysis.scenario_scores(scores)
    result = score_analysis.analyze(scenarios)
    result["judge_agreement"] = {
        field: {"unanimous_units": sum(len(set(r["judge_votes"][field])) == 1 for r in semantic_rows),
                "majority_split_units": sum(len(set(r["judge_votes"][field])) > 1 for r in semantic_rows),
                "total_units": len(semantic_rows)} for field in score_analysis.SEMANTIC_FIELDS}
    output.mkdir(parents=True)
    score_analysis.write_jsonl(output / "unit-scores.jsonl", scores)
    score_analysis.write_jsonl(output / "scenario-scores.jsonl", scenarios)
    result["deterministic_execution"] = {
        c: proportion(sum(all(r["component_both_repetitions"][k] for k in
                              ("correct_co_or_response", "ally_preserved", "valid_target_selected", "talk_vote_consistent"))
                          for r in scenarios if r["condition"] == c and r["scenario_class"] == "established_pp"), 80)
        for c in score_analysis.CONDITIONS}
    result["files"] = {"inputs.jsonl": digest(corpus / "inputs.jsonl"), "labels.jsonl": digest(corpus / "labels.jsonl"),
                       "semantic-scores.jsonl": digest(run / "judge-inputs-v1/semantic-scores.jsonl"),
                       "unit-scores.jsonl": digest(output / "unit-scores.jsonl"),
                       "scenario-scores.jsonl": digest(output / "scenario-scores.jsonl"),
                       "scorer.py": digest(Path(score_analysis.__file__))}
    write(output / "analysis.json", result)
    write(output / "cue-strata-audit.json", create_artifact(corpus / "inputs.jsonl", corpus / "labels.jsonl", output / "scenario-scores.jsonl"))
    return result, units, items


def differences(old, new, path=""):
    if isinstance(old, dict) and isinstance(new, dict):
        return [change for key in sorted(old.keys() | new.keys())
                for change in differences(old.get(key), new.get(key), f"{path}/{key}")]
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [change for i, (a, b) in enumerate(zip(old, new)) for change in differences(a, b, f"{path}/{i}")]
    return [] if old == new else [{"path": path, "old": old, "new": new}]


def ledger(a, b, fixed_units, fixed_items):
    """Log extraction inputs without condition, true roles, votes, or outcomes."""
    old_path = a / "source/experiments/pp/fixed_boards/score_confirmation.py"
    old = ModuleType("_archived_pp_extractor")
    old.__file__ = str(old_path)
    exec(compile(old_path.read_bytes(), str(old_path), "exec"), old.__dict__)
    records = []
    def add(source, text, names):
        inputs = {"text": text, "names": sorted(names)}
        import hashlib
        rid = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        records.append({"extraction_id": rid, "source": source, "input": inputs,
                        "old": {"target": old.named_target(text, names), "co": old.role_co(text)},
                        "corrected": {"target": named_target(text, names), "co": role_co(text)},
                        "target_evidence": vote_target_details(text, names)})
    for unit in sorted(fixed_units.values(), key=lambda r: r["unit_id"]):
        add(f"original-fixed/{unit['unit_id']}", unit["talk"], fixed_items[unit["scenario_id"]]["input"]["snapshot"]["players"])
    items = {r["scenario_id"]: r for r in jsonl(a / "inputs.jsonl")}
    for group in read(a / "schedule.json"):
        add(f"A/{group['group_id']}", read(a / "groups" / group["group_id"] / "talk.json")["output"],
            items[group["scenario_id"]]["input"]["snapshot"]["players"])
    for block in read(b / "schedule.json"):
        for cell in block["conditions"]:
            row = f"{block['block_id']}-{cell}"
            game = read(b / row_result(b, row)["game_path"])
            roles = final_role_map(game)
            entries = [(i, e, json.loads(e["request"])) for i, e in enumerate(game["entries"])]
            reached = None
            for _, _, request in entries:
                info = request.get("info") or {}
                alive = [n for n, status in (info.get("status_map") or {}).items() if status == "ALIVE"]
                if len(alive) == 3 and {"WEREWOLF", "POSSESSED"} <= {roles[n] for n in alive}:
                    reached = info["day"], alive
                    break
            if not reached:
                continue
            day, alive = reached
            team = {n for n in alive if roles[n] in ("WEREWOLF", "POSSESSED")}
            voted = set()
            for index, entry, request in entries:
                actor = entry["agent"]
                if (request.get("info") or {}).get("day") != day or actor not in team or actor in voted:
                    continue
                if request["request"] == "VOTE":
                    voted.add(actor)
                elif request["request"] == "TALK":
                    add(f"B/{row}/entry-{index}", str(entry.get("response") or "").strip(), alive)
    return records


def tables(output, fixed, a, b):
    for label, result, values in (("original-fixed", fixed, {c: r["composite_pp_success"] for c, r in fixed["outcome_summary"].items()}),
                                   ("A", a, {c: r["consistent"] for c, r in a["rates"].items()}),
                                   ("B", b, b["rates_complete_blocks"])):
        lines = [f"# Corrected {label}", "", "| Condition | Successes | Total | Rate |", "| --- | ---: | ---: | ---: |"]
        lines += [f"| {c} | {v['numerator']} | {v['denominator']} | {v['proportion']} |" for c, v in values.items()]
        lines += ["", "Post-outcome deterministic scoring correction; original model outputs and semantic labels retained.",
                  "Full corrected counts and statistical intervals: analysis.json."]
        (output / label / "tables.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--a", type=Path, default=DEFAULT_A)
    parser.add_argument("--b", type=Path, default=DEFAULT_B)
    parser.add_argument("--original-fixed", type=Path, default=DEFAULT_FIXED)
    parser.add_argument("--original-normal", type=Path, default=DEFAULT_NORMAL)
    args = parser.parse_args()
    output = args.output.resolve()
    source_runs = [args.a.resolve(), args.b.resolve(), args.original_fixed.resolve(), args.original_normal.resolve()]
    protected = source_runs + [ORIGINAL_JUDGE, ROOT / "src", ROOT / "experiments/pp"]
    if any(output == r or output.is_relative_to(r) or r.is_relative_to(output) for r in protected):
        raise ValueError("Output must be separate from all original runs")
    if any((output / name).exists() for name in ("manifest.json", "original-fixed", "original-normal", "A", "B", "source")):
        raise FileExistsError("Refusing to overwrite a previous correction; choose a new output directory")
    a_run, b_run, fixed_run, normal_run = source_runs
    sources = analysis_sources()
    source_hashes = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    print("Verifying A/B frozen inputs and saved source snapshots...", flush=True)
    ma, fa = verify_archived_revision(a_run)
    mb, fb = verify_archived_revision(b_run)
    if (ma.get("kind"), mb.get("kind"), ma.get("purpose"), mb.get("purpose")) != ("A", "B", "main", "main"):
        raise ValueError("This correction requires the main A and B evaluations")
    if len(read(a_run / "schedule.json")) != 480 or len(read(b_run / "schedule.json")) != 200:
        raise ValueError("Expected 480 A TALK groups and 200 B blocks")
    original_inputs = checkpoint_inputs([fixed_run, normal_run, ORIGINAL_JUDGE])
    print(f"Verified A/B evidence and {len(original_inputs)} original-sample files.", flush=True)
    output.mkdir(parents=True, exist_ok=True)
    input_hashes = dict(original_inputs)
    corpus = ROOT / read(fixed_run / "manifest.json")["corpus_dir"]
    for path in (corpus / "inputs.jsonl", corpus / "labels.jsonl", OLD_NORMAL_ANALYSIS):
        input_hashes[str(path.relative_to(ROOT))] = digest(path)
    for run in (a_run, b_run):
        for name in ("manifest.json", "frozen.json", "analysis/analysis.json"):
            input_hashes[str((run / name).relative_to(ROOT))] = digest(run / name)
    write(output / "input-manifest.json", input_hashes)
    shutil.copy2(PROTOCOL, output / PROTOCOL.name)
    print("Recomputing original fixed-board scores and statistics...", flush=True)
    fixed, units, items = original_fixed(fixed_run, output / "original-fixed")
    print("Recomputing A/B scores and statistics...", flush=True)
    a, b = analyze_a(a_run, fa), analyze_b(b_run, fb)
    for name, result, run, manifest in (("A", a, a_run, ma), ("B", b, b_run, mb)):
        result.update(purpose=manifest["purpose"], run_id=manifest["run_id"], frozen_sha256=digest(run / "frozen.json"))
        write(output / name / "analysis.json", result)
    # Distinct legacy normal-game heuristic is deliberately preserved.
    subprocess.run([sys.executable, "-m", "experiments.pp.v2.normal.analyze_results", str(normal_run),
                    str(output / "original-normal"), "--fixed-analysis", str(output / "original-fixed/analysis.json")], cwd=ROOT, check=True)
    print("Recording all utterance extractions and old/new differences...", flush=True)
    extraction_records = ledger(a_run, b_run, units, items)
    score_analysis.write_jsonl(output / "extractions.jsonl", extraction_records)
    score_analysis.write_jsonl(output / "changed-extractions.jsonl", [r for r in extraction_records if r["old"] != r["corrected"]])
    review = {r["extraction_id"]: {"extraction_id": r["extraction_id"], **r["input"], **r["corrected"]} for r in extraction_records}
    score_analysis.write_jsonl(output / "review-inputs.jsonl", [review[k] for k in sorted(review)])
    changes = {name: differences(read(old_path), result) for name, old_path, result in
               (("original-fixed", fixed_run / "analysis-v1/analysis.json", fixed),
                ("A", a_run / "analysis/analysis.json", a), ("B", b_run / "analysis/analysis.json", b),
                ("original-normal", OLD_NORMAL_ANALYSIS, read(output / "original-normal/analysis.json")))}
    write(output / "changes.json", changes)
    tables(output, fixed, a, b)
    verify_hashes(ROOT, source_hashes)
    for src in sources:
        dest = output / "source" / src.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    # Detect any accidental input mutation during the analysis itself.
    verify_hashes(ROOT, input_hashes)
    verify_archived_revision(a_run)
    verify_archived_revision(b_run)
    write(output / "manifest.json", {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "api_calls": 0,
        "correction_type": "post-outcome deterministic extraction correction",
        "scoring_rules_version": SCORING_RULES_VERSION,
        "original_data_modified": False, "semantic_judgments_reused": True,
        "source_runs": {k: str(p.relative_to(ROOT)) for k, p in zip(("A", "B", "original-fixed", "original-normal"), source_runs)},
        "extraction_records": len(extraction_records), "unique_extraction_inputs": len(review),
        "changed_extraction_records": sum(r["old"] != r["corrected"] for r in extraction_records),
        "source_hashes": source_hashes,
        "files": {str(p.relative_to(output)): digest(p) for folder in ("original-fixed", "original-normal", "A", "B", "source")
                  for p in (output / folder).rglob("*") if p.is_file()},
        "audit_files": {name: digest(output / name) for name in ("input-manifest.json", "changes.json", "extractions.jsonl", "changed-extractions.jsonl", "review-inputs.jsonl", PROTOCOL.name)},
    })
    print(f"Corrected analysis saved to {output}; {len(extraction_records)} utterance records; API calls: 0")


if __name__ == "__main__":
    main()
