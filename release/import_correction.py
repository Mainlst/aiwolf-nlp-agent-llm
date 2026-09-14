#!/usr/bin/env python3
"""Build a new dataset from a verified release and a staged scoring correction."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.pp.fixed_boards.scoring_rules import SCORING_RULES_VERSION
from release.export import sanitize
from release.reproduce import compare, read, safe_path, sha256, verify_dataset, verify_files, write

REFERENCES = {
    'fixed': 'original-fixed/analysis.json',
    'normal': 'original-normal/analysis.json',
    'stage': 'A/analysis.json',
    'factorial': 'B/analysis.json',
    'cue': 'original-fixed/cue-strata-audit.json',
}


def validate_correction(data, source):
    """Validate the formal correction and its relationship to the exported evidence."""
    verify_dataset(data)
    base = read(data / 'provenance/release-manifest.json')
    if 'correction' in base['runs']:
        raise ValueError('This dataset already includes a correction')
    formal = read(source / 'manifest.json')
    if formal['scoring_rules_version'] != SCORING_RULES_VERSION:
        raise ValueError('Scoring version mismatch')
    if formal['api_calls'] != 0 or formal['original_data_modified'] or not formal['semantic_judgments_reused']:
        raise ValueError('Correction must reuse the unchanged saved outputs and judgments')
    verify_files(source, formal['files'])
    verify_files(source, formal['audit_files'])
    rules = 'experiments/pp/fixed_boards/scoring_rules.py'
    if formal['source_hashes'][rules] != sha256(ROOT / rules):
        raise ValueError('Current extraction rules differ from the verified correction')
    inputs = read(source / 'input-manifest.json')
    matched = 0
    for member in base['members']:
        key = f"experiments/runs/{base['runs'][member['run']]}/{member['path']}"
        if key in inputs:
            if inputs[key] != member['source_sha256']:
                raise ValueError(f'Correction input differs from release source: {key}')
            matched += 1
    for run in ('fixed', 'fixed_judge', 'normal', 'stage', 'factorial'):
        key = f"experiments/runs/{base['runs'][run]}/manifest.json"
        if key not in inputs:
            raise ValueError(f'Missing original input manifest: {run}')
    for name in ('inputs.jsonl', 'labels.jsonl'):
        if inputs[f'experiments/pp/fixed_boards_v2/generated/{name}'] != sha256(data / 'boards' / name):
            raise ValueError('Correction board corpus differs from the release')
    if formal['extraction_records'] != 2792 or formal['changed_extraction_records'] != 377:
        raise ValueError('Unexpected extraction ledger coverage')
    for key, field in [('fixed', 'primary_comparison'), ('stage', 'primary'), ('factorial', 'primary')]:
        compare(read(data / 'results' / f'{key}.json')[field], read(source / REFERENCES[key])[field], f'{key}/primary')
    old_games = read(data / 'results/factorial.json')['games']
    games = read(source / REFERENCES['factorial'])['games']
    compare([{k: v for k, v in g.items() if k != 'diagnostics'} for g in games],
            [{k: v for k, v in g.items() if k != 'diagnostics'} for g in old_games], 'selected-games')
    return base, formal, matched


def build(data, source, output, upstream_commit):
    for path in (data, source, output):
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError('Stage inputs and outputs inside this checkout')
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in (data, source)):
        raise ValueError('Choose a new output directory separate from the inputs')
    base, formal, matched = validate_correction(data, source)
    shutil.copytree(data, output)
    selected = set(formal['files']) | set(formal['audit_files']) | {'manifest.json'}
    # Optional review evidence is preserved with separate source/export hashes.
    selected.update(p.relative_to(source).as_posix() for p in (source / 'verification').rglob('*') if p.is_file())
    transforms = []
    correction_members = []
    with (output / 'raw/correction.tar.gz').open('wb') as stream:
        with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='', compresslevel=1) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|') as archive:
                for relative in sorted(selected):
                    path = safe_path(source, relative)
                    if path.is_symlink() or path.name.startswith('.env'):
                        raise ValueError('Unsupported correction member')
                    before = path.read_bytes()
                    after = sanitize(before)
                    info = tarfile.TarInfo(relative)
                    info.size, info.mode, info.mtime = len(after), 0o644, 0
                    archive.addfile(info, io.BytesIO(after))
                    entry = {'run': 'correction', 'path': relative, 'source_sha256': hashlib.sha256(before).hexdigest(),
                             'sha256': hashlib.sha256(after).hexdigest(), 'bytes': len(after)}
                    correction_members.append(entry)
                    if before != after:
                        transforms.append({**entry, 'operation': 'local-path normalization / credential redaction'})
    history = output / 'results/original'
    history.mkdir()
    for path in sorted((output / 'results').glob('*.json')):
        shutil.copy2(path, history / path.name)
    for key, relative in REFERENCES.items():
        (output / 'results' / f'{key}.json').write_bytes(sanitize((source / relative).read_bytes()))
    # The original normal-game heuristic and partner protocol audit are unchanged.
    games = read(output / 'results/factorial.json')['games']
    old_view = output / 'data/factorial_games.jsonl'
    shutil.copy2(old_view, history / 'factorial_games.jsonl')
    old_view.write_text(''.join(json.dumps(g, ensure_ascii=False, sort_keys=True) + '\n' for g in games))
    base['runs']['correction'] = source.name
    base['members'].extend(correction_members)
    metadata = {'scoring_rules_version': SCORING_RULES_VERSION, 'upstream_commit': upstream_commit,
                'source_manifest_sha256': sha256(source / 'manifest.json'),
                'previous_dataset_checksums_sha256': sha256(data / 'checksums.sha256'),
                'matched_original_input_hashes': matched, 'post_outcome_correction': True,
                'original_data_modified': False, 'semantic_judgments_reused': True,
                'extraction_records': formal['extraction_records'],
                'changed_extraction_records': formal['changed_extraction_records'],
                'references': REFERENCES, 'previous_results': 'results/original',
                'unchanged_archives': {p.name: sha256(p) for p in sorted((data / 'raw').glob('*.tar.gz'))}}
    base['scoring_correction'] = metadata
    write(output / 'provenance/release-manifest.json', base)
    write(output / 'provenance/scoring-correction.json', metadata)
    with (output / 'provenance/transformations.jsonl').open('a') as stream:
        stream.writelines(json.dumps(t, sort_keys=True) + '\n' for t in transforms)
    # Verify the staged source again before sealing the new dataset inventory.
    verify_files(source, formal['files'])
    verify_files(source, formal['audit_files'])
    paths = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'checksums.sha256')
    (output / 'checksums.sha256').write_text(''.join(f'{sha256(p)}  {p.relative_to(output).as_posix()}\n' for p in paths))
    print(f'Correction exported: {len(correction_members)} members; {matched} original source hashes matched')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--correction', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--upstream-commit', required=True)
    args = parser.parse_args()
    build(args.data.resolve(), args.correction.resolve(), args.output.resolve(), args.upstream_commit)


if __name__ == '__main__':
    main()
