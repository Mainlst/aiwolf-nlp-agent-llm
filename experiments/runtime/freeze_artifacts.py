#!/usr/bin/env python3
"""Hash completed run artifacts into a non-secret post-run manifest."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    """Return a file digest without interpreting its contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    """Create artifact-manifest.json once after all analyses are complete."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = run_dir / "artifact-manifest.json"
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite frozen artifacts: {output}")

    forbidden = [path for path in run_dir.rglob("*") if path.is_file() and path.name in {".env", "config.env"}]
    if forbidden:
        raise ValueError(f"Secret-bearing filenames found: {forbidden}")
    files = {
        str(path.relative_to(run_dir)): {"sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and path != output
    }
    payload = {
        "schema_version": 1,
        "frozen_at": datetime.now(UTC).astimezone().isoformat(),
        "run_dir": str(run_dir),
        "file_count": len(files),
        "files": files,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
