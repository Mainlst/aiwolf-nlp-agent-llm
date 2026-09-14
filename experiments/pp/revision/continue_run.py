"""Create an auditable continuation; keep paid valid rows and retain failed evidence."""

import argparse
import shutil
from pathlib import Path

from experiments.pp.revision.common import digest, lock, read, spec, write
from experiments.pp.revision.normal import audit_game, complete_game, operational_failures
from experiments.pp.revision.prepare import prepare


def continue_run(source, target, settings, checkpoint):
    source, target = Path(source).resolve(), Path(target).resolve()
    checkpoint = read(checkpoint)
    if checkpoint["source_run"] != str(source):
        raise ValueError("Checkpoint belongs to another run")
    with lock(source):
        actual = {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file() and p.name != ".lock"}
        if actual != set(checkpoint["files"]):
            raise ValueError("Source evidence file set changed")
        for name, expected in checkpoint["files"].items():
            if digest(source / name) != expected:
                raise ValueError(f"Source evidence changed: {name}")
        manifest = read(source / "manifest.json")
        for name, expected in manifest["files"].items():
            if digest(source / name) != expected:
                raise ValueError(f"Source frozen artifact changed: {name}")
        for name, expected in manifest["source_hashes"].items():
            if digest(source / "source" / name) != expected:
                raise ValueError(f"Source snapshot changed: {name}")
        if manifest["kind"] != "B" or manifest["purpose"] != "main" or (source / "frozen.json").exists():
            raise ValueError("Only an unfrozen main B can be continued")
        prepare(
            argparse.Namespace(
                run_dir=target,
                kind="B",
                purpose="main",
                spec=Path(settings),
                server_config=Path(manifest["server_cwd"]) / "config/default_en_5.yml",
                server_binary=Path(manifest["server_binary"]),
                b_run=None,
            )
        )
        schedule = read(source / "schedule.json")
        if read(target / "schedule.json") != schedule:
            raise ValueError("Continuation must preserve schedule and assignments")
        # Only execution settings may change. In particular, endpoints, dates,
        # number of games, policies, and randomization are not selected by outcome.
        old, new = spec(source), spec(target)
        for key in (
            "blocks_per_wave",
            "max_parallel",
            "max_attempts",
            "resilient",
            "memory_reserve_gib",
            "memory_per_game_gib",
            "retry_backoff_seconds",
        ):
            old["normal"].pop(key, None)
            new["normal"].pop(key, None)
        if old != new:
            raise ValueError("Scientific specification or deadlines changed")
        for p in (source / "cells").glob("*/*.yml"):
            if digest(p) != digest(target / p.relative_to(source)):
                raise ValueError("Policy changed")
        kept, repair, imported, decisions = [], [], {}, {}
        blocks = {b["block_id"]: b for b in schedule}
        for row_dir in sorted((source / "attempts").iterdir()):
            if not row_dir.is_dir() or row_dir.name.startswith("_"):
                continue
            block_id, cell = row_dir.name.split("-")
            good = False
            for folder in sorted(row_dir.iterdir()):
                if not folder.is_dir() or not (folder / "start.json").exists():
                    continue
                destination = target / folder.relative_to(source)
                shutil.copytree(folder, destination)
                prior = read(folder / "result.json") if (folder / "result.json").exists() else {}
                game_path = complete_game(folder)
                reasons = operational_failures(folder / "logs")
                if game_path is not None:
                    reasons = audit_game(read(game_path), blocks[block_id]["roster"], folder / "logs", cell)
                valid = prior.get("status") == "complete" and game_path is not None and not reasons
                if valid:
                    if prior.get("game_sha256") != digest(game_path):
                        raise ValueError("Saved terminal hash mismatch")
                    good = True
                    for p in destination.rglob("*"):
                        if p.is_file():
                            imported[str(p.relative_to(target))] = digest(p)
                else:
                    write(destination / "source-result.json", prior)
                    write(
                        destination / "result.json",
                        {
                            "status": "retryable",
                            "reasons": reasons or ["source_incomplete"],
                            "continuation_reaudit": True,
                            "terminal_game": False,
                        },
                    )
                decisions[str(folder.relative_to(source))] = {
                    "kept": valid,
                    "reasons": reasons,
                    "original_status": prior.get("status"),
                }
            (kept if good else repair).append(row_dir.name)
        lineage = target / "lineage"
        write(lineage / "source-evidence.json", checkpoint)
        write(lineage / "source-manifest.json", manifest)
        write(lineage / "source-state.json", read(source / "state.json"))
        write(
            lineage / "source-critical.json", {p.name: read(p) for p in (source / "attempts/_critical").glob("*.json")}
        )
        write(lineage / "imported-evidence.json", imported)
        write(
            lineage / "continuation.json",
            {
                "source_run": str(source),
                "kept_rows": kept,
                "repair_rows": repair,
                "decisions": decisions,
                "authorization": "User requested repair, lower parallelism and continuation",
                "protocol_change": "Resilient transport, action audit, 8 parallel, max 3 game attempts including history",
            },
        )
        state = {"started": read(source / "state.json")["started"], "schedule_finished": False, "critical_stop": False}
        write(target / "state.json", state)
        current = read(target / "manifest.json")
        current["continuation"] = True
        current["files"].update({str(p.relative_to(target)): digest(p) for p in lineage.glob("*.json")})
        write(target / "manifest.json", current)
        print(f"Continuation prepared: retained={len(kept)}, repair={len(repair)}, no API calls")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "target", "spec", "checkpoint"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    continue_run(args.source, args.target, args.spec, args.checkpoint)


if __name__ == "__main__":
    main()
