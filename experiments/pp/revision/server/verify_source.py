#!/usr/bin/env python3
"""Verify the preserved server source without network access or server execution."""

import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "source-manifest.json").read_text())
    expected = {entry["path"]: entry for entry in manifest["files"]}
    source = root / "source"
    actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
    if actual != set(expected):
        raise ValueError("Server source inventory differs from its manifest")
    for name, entry in expected.items():
        path = source / name
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("Unexpected server source path")
        data = path.read_bytes()
        git_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or git_blob != entry["git_blob_sha1"]:
            raise ValueError(f"Server source hash mismatch: {name}")
    patch_hash = hashlib.sha256((root / "seat-assignment.patch").read_bytes()).hexdigest()
    if patch_hash != manifest["patch_sha256"]:
        raise ValueError("Server patch hash mismatch")
    print(f"Verified {len(expected)} historical server source files and the seat-assignment patch")


if __name__ == "__main__":
    main()
