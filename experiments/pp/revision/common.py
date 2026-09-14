"""Offline provenance, locking, credentials, and process helpers."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CELLS = ("G00", "G10", "G01", "G11")
POLICIES = {"0": "P-PP", "1": "P+"}
TEAMS = ("revisionWolf", "revisionPossessed", "revisionSeer", "revisionVillagerA", "revisionVillagerB")
ROLES = ("WEREWOLF", "POSSESSED", "SEER", "VILLAGER", "VILLAGER")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()] if Path(path).exists() else []


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc)


def before(iso, clock=None):
    return (clock or now()) < datetime.fromisoformat(iso)


def seconds_left(iso):
    return max(0.0, (datetime.fromisoformat(iso) - now()).total_seconds())


def load_credentials():
    """Use the explicitly authorized file, overriding an inherited key silently."""
    from dotenv import dotenv_values

    key = dotenv_values(ROOT / "config/.env").get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is absent from config/.env")
    os.environ["OPENAI_API_KEY"] = key


def spec(run_dir):
    return yaml.safe_load((Path(run_dir) / "spec.yml").read_text())


@contextmanager
def lock(run_dir):
    """Prevent competing runners, freezing, or analysis of a live run."""
    with (Path(run_dir) / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def verify(run_dir):
    """Check frozen inputs and the entire relevant source tree, without API calls."""
    run_dir = Path(run_dir)
    manifest = read(run_dir / "manifest.json")
    for relative, expected in manifest["files"].items():
        if digest(run_dir / relative) != expected:
            raise ValueError(f"Frozen artifact changed: {relative}")
    for relative, expected in manifest["source_hashes"].items():
        if digest(ROOT / relative) != expected:
            raise ValueError(f"Runtime source changed; prepare a new run: {relative}")
    if manifest["kind"] == "B":
        expected = spec(run_dir)["normal"].get("required_server_binary_sha256")
        if not expected or manifest["server_binary_sha256"] != expected:
            raise ValueError("Server binary does not match the required seatfixed SHA-256; prepare a new run")
        if digest(Path(manifest["server_binary"])) != expected:
            raise ValueError("Server binary changed")
    return manifest


def child(command, output, timeout):
    """Run an isolated process group; a deadline also kills its server/clients."""
    if timeout <= 0:
        return 124
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with Path(output).open("w") as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124
        finally:
            # Also clean up children if the launcher failed unexpectedly.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def verify_imported(run_dir):
    path = Path(run_dir) / "lineage/imported-evidence.json"
    if path.exists():
        for name, expected in read(path).items():
            if digest(Path(run_dir) / name) != expected:
                raise ValueError(f"Imported successful evidence changed: {name}")


def freeze(run_dir):
    """Freeze eligibility and all generated evidence before showing outcomes."""
    run_dir = Path(run_dir)
    with lock(run_dir):
        manifest = verify(run_dir)
        verify_imported(run_dir)
        if (run_dir / "frozen.json").exists():
            raise ValueError("Already frozen")
        state = read(run_dir / "state.json")
        settings = spec(run_dir)
        deadline = settings["normal" if manifest["kind"] == "B" else "fixed"]["data_deadline"]
        if before(deadline) and not state.get("schedule_finished"):
            raise ValueError("Finish the schedule or wait for the fixed data deadline")
        # Include missing entries explicitly. No outcome is used for selection.
        write(
            run_dir / "frozen.json",
            {
                "frozen_at": now().isoformat(),
                "manifest_sha256": digest(run_dir / "manifest.json"),
                "state": state,
                "evidence": {
                    str(p.relative_to(run_dir)): digest(p)
                    for folder in ("attempts", "units", "groups", "judge")
                    for p in sorted((run_dir / folder).rglob("*"))
                    if p.is_file()
                },
            },
        )


def verify_frozen(run_dir):
    manifest = verify(run_dir)
    frozen = read(Path(run_dir) / "frozen.json")
    if frozen["manifest_sha256"] != digest(Path(run_dir) / "manifest.json"):
        raise ValueError("Frozen manifest changed")
    current_evidence = {
        str(p.relative_to(run_dir))
        for folder in ("attempts", "units", "groups", "judge")
        for p in (Path(run_dir) / folder).rglob("*")
        if p.is_file()
    }
    if current_evidence != set(frozen["evidence"]):
        raise ValueError("Evidence was added or removed after freezing")
    for relative, expected in frozen["evidence"].items():
        if digest(Path(run_dir) / relative) != expected:
            raise ValueError(f"Frozen evidence changed: {relative}")
    return manifest, frozen
