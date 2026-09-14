#!/usr/bin/env python3
"""Audit sealed working files, actual distribution ZIPs, and dataset members."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release.export import SECRET
from release.package import ARCHIVE_NAME, MANIFEST, digest, paths, verify_manifest


def inspect_distribution(root):
    root = Path(root)
    findings = []
    counts = {'code_files': 0, 'dataset_files': 0, 'archive_members': 0,
              'zip_archives': 0, 'zip_members': 0}

    def finding(name, reason):
        findings.append({'path': name, 'reasons': [reason]})

    def scan(name, data):
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            return
        if SECRET.search(text):
            finding(name, 'credential pattern')
        # Match concrete user paths, not sanitizer source-code regexes.
        if re.search(r'/home/[A-Za-z0-9_.-]+/', text):
            finding(name, 'original home path')

    try:
        candidates = list(paths(root))
        verify_manifest(root)
    except (OSError, ValueError):
        candidates = []
        finding(MANIFEST, 'invalid package inventory or code manifest')
    expected = {p.relative_to(root).as_posix(): p for p in candidates}
    for name, path in expected.items():
        counts['code_files'] += 1
        scan(name, path.read_bytes())

    artifacts = {}
    dist = root / 'dist'
    archives = [] if dist.is_symlink() else sorted(dist.glob('*.zip'))
    if not archives or ARCHIVE_NAME not in {p.name for p in archives}:
        finding('dist', 'missing current distribution ZIP or unsafe distribution directory')
    for path in archives:
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.is_file():
            finding(name, 'non-regular distribution archive')
            continue
        artifacts[name] = digest(path)
        counts['zip_archives'] += 1
        try:
            with zipfile.ZipFile(path) as archive:
                seen = set()
                for member in archive.infolist():
                    label = f'{name}:{member.filename}'
                    p = PurePosixPath(member.filename)
                    mode = stat.S_IFMT(member.external_attr >> 16)
                    if (p.is_absolute() or '..' in p.parts or not p.parts or member.is_dir()
                            or mode not in (0, stat.S_IFREG) or member.filename in seen):
                        finding(label, 'unsafe, duplicate, or non-regular ZIP member')
                        continue
                    seen.add(member.filename)
                    payload = archive.read(member)
                    counts['zip_members'] += 1
                    scan(label, payload)
                    source = expected.get(member.filename)
                    if source is None or payload != source.read_bytes():
                        finding(label, 'ZIP member differs from working-tree package inventory')
                if seen != set(expected):
                    finding(name, 'ZIP file inventory differs from working tree')
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
            finding(name, 'unreadable or corrupt ZIP')

    checksum = dist / 'checksums.sha256'
    recorded = {}
    try:
        if checksum.is_symlink() or dist.is_symlink():
            raise ValueError('Symlink checksum')
        for line in checksum.read_text().splitlines():
            value, name = line.split('  ', 1)
            if name in recorded or not re.fullmatch(r'[0-9a-f]{64}', value):
                raise ValueError('Invalid checksum')
            recorded[name] = value
        if recorded != artifacts:
            raise ValueError('Checksum mismatch')
    except (OSError, ValueError):
        finding('dist/checksums.sha256', 'ZIP checksums missing, invalid, or mismatched')

    data = root / 'dataset'
    if not data.is_dir() or data.is_symlink():
        finding('dataset', 'missing or unsafe dataset directory')
        dataset_files = []
    else:
        dataset_files = sorted(data.rglob('*'))
    for path in dataset_files:
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            finding(name, 'dataset symlink')
            continue
        if not path.is_file():
            continue
        counts['dataset_files'] += 1
        if path.name.endswith('.tar.gz'):
            try:
                with tarfile.open(path, 'r|gz') as archive:
                    seen = set()
                    for member in archive:
                        p = PurePosixPath(member.name)
                        if (not member.isfile() or p.is_absolute() or '..' in p.parts
                                or not p.parts or member.name in seen):
                            finding(f'{name}:{member.name}', 'unsafe, duplicate, or non-regular TAR member')
                            continue
                        seen.add(member.name)
                        counts['archive_members'] += 1
                        scan(f'{name}:{member.name}', archive.extractfile(member).read())
            except (OSError, tarfile.TarError):
                finding(name, 'unreadable or corrupt TAR')
        else:
            scan(name, path.read_bytes())
    return {'status': 'passed' if not findings else 'failed', 'counts': counts,
            'findings': findings, 'zip_sha256': artifacts,
            'scope': 'Sealed package inventory, actual dist ZIP members and their agreement with the working tree, ZIP checksums, and dataset files/TAR members; known credential patterns and concrete /home paths. Git history and data licensing are outside this audit.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/distribution-audit.json')
    args = parser.parse_args()
    output = args.output.resolve()
    # Keep the final report outside both distributions to avoid a self-reference.
    if (not output.is_relative_to(ROOT) or output.is_relative_to(ROOT / 'dataset')
            or output in {p.resolve() for p in paths(ROOT)}
            or output.name == 'checksums.sha256' or output.suffix == '.zip'):
        raise ValueError('Audit output must be inside this checkout but outside the packaged files and dataset')
    result = inspect_distribution(ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if result['findings']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
