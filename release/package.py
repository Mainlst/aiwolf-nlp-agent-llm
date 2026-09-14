#!/usr/bin/env python3
"""Package an explicitly inventoried, sealed release without requiring Git."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = 'camera-ready-code-1.0.0-rc.3.zip'
INVENTORY = 'release/package-files.txt'
MANIFEST = 'release/code-manifest.json'
# The manifest cannot hash itself. The reproduction report records its digest.
UNSEALED = {MANIFEST, 'docs/verification.json'}
DIRECTORIES = {'src', 'experiments', 'release', 'config', 'docs', 'tests'}
FILES = {'AGENTS.md', 'README.md', 'README.ja.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
         'CITATION.cff', 'pyproject.toml', 'uv.lock', '.python-version', '.gitignore'}
EXCLUDED = {'runs', '__pycache__', 'venv', 'env', 'build', 'dist', 'backup', 'backups',
            'release-backups'}
EXCLUDED_SUFFIXES = {'.zip', '.pyc', '.pyo', '.aux', '.blg', '.log', '.out', '.xdv',
                     '.fls', '.fdb_latexmk', '.bak', '.backup', '.old', '.orig', '.swp', '.swo'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def checked_path(root, relative):
    """Reject unsafe inventory entries and symlinks at every path component."""
    p = PurePosixPath(relative)
    if not p.parts or p.is_absolute() or '..' in p.parts or p.as_posix() != relative:
        raise ValueError('Unsafe package inventory path')
    if len(p.parts) == 1:
        if relative not in FILES:
            raise ValueError(f'Unsupported package file: {relative}')
    elif p.parts[0] not in DIRECTORIES:
        raise ValueError(f'Unsupported package directory: {relative}')
    if any(part in EXCLUDED or part.startswith('.') for part in p.parts[:-1]):
        raise ValueError(f'Excluded package directory: {relative}')
    if ((p.name.startswith('.') and relative not in {'.gitignore', '.python-version',
                                                     'experiments/.gitignore', 'config/.env.example'})
            or p.name.endswith('~') or p.suffix.lower() in EXCLUDED_SUFFIXES):
        raise ValueError(f'Excluded package file: {relative}')
    path = root
    for part in p.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f'Do not package symlinks: {relative}')
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f'Missing or unsafe package file: {relative}')
    return path


def paths(root=None):
    root = Path(root) if root is not None else ROOT
    inventory = checked_path(root, INVENTORY)
    names = [line.strip() for line in inventory.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if len(names) != len(set(names)) or INVENTORY not in names or MANIFEST not in names:
        raise ValueError('Package inventory must be unique and include itself and the code manifest')
    for name in sorted(names):
        yield checked_path(root, name)


def sealed_paths(root=None):
    root = Path(root) if root is not None else ROOT
    return [p for p in paths(root) if p.relative_to(root).as_posix() not in UNSEALED]


def verify_manifest(root):
    candidates = list(paths(root))
    expected = json.loads((root / MANIFEST).read_text())
    actual = {p.relative_to(root).as_posix(): digest(p)
              for p in candidates if p.relative_to(root).as_posix() not in UNSEALED}
    if expected != actual:
        raise ValueError('Code manifest differs from the package inventory or file contents; review and seal first')
    return candidates


def create_package(root):
    root = Path(root)
    candidates = verify_manifest(root)
    directory = root / 'dist'
    checksum = directory / 'checksums.sha256'
    if directory.is_symlink() or checksum.is_symlink():
        raise ValueError('Do not write distribution files through symlinks')
    directory.mkdir(exist_ok=True)
    destination = directory / ARCHIVE_NAME
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    archives = sorted(directory.glob('*.zip'))
    if any(p.is_symlink() or not p.is_file() for p in archives):
        raise ValueError('Unsupported distribution archive')
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in candidates:
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            permissions = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.external_attr = (stat.S_IFREG | permissions) << 16
            archive.writestr(info, path.read_bytes())
    archives.append(destination)
    checksum.write_text(''.join(f'{digest(p)}  {p.relative_to(root).as_posix()}\n'
                                for p in sorted(archives)))
    return destination


def main():
    print(create_package(ROOT).relative_to(ROOT))


if __name__ == '__main__':
    main()
