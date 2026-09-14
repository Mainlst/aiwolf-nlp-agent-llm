#!/usr/bin/env python3
"""Seal the reviewed package inventory and dataset; does not authenticate authorship."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release.package import MANIFEST, digest, sealed_paths


def seal_code(root):
    sources = sealed_paths(root)
    (root / MANIFEST).write_text(json.dumps(
        {p.relative_to(root).as_posix(): digest(p) for p in sources},
        indent=2, sort_keys=True) + '\n')
    return len(sources)


def main():
    count = seal_code(ROOT)
    data = ROOT / 'dataset'
    if not data.is_dir() or data.is_symlink():
        raise ValueError('Expected a local dataset directory')
    all_paths = sorted(data.rglob('*'))
    if any(p.is_symlink() for p in all_paths):
        raise ValueError('Do not seal dataset symlinks')
    files = [p for p in all_paths if p.is_file() and p.name != 'checksums.sha256']
    (data / 'checksums.sha256').write_text(''.join(
        f'{digest(p)}  {p.relative_to(data).as_posix()}\n' for p in files))
    print(f'Sealed {count} package files and {len(files)} dataset files; package.py updates ZIP checksums')


if __name__ == '__main__':
    main()
