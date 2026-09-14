#!/usr/bin/env python3
"""Export the five selected local runs; never read or write outside this checkout."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    'fixed': 'pp-v2-fixed-analysis-reachable-v4-luna-20260816-v1',
    'fixed_judge': 'pp-v2-fixed-judge-reachable-v4-sol-20260816-v1',
    'normal': 'pp-v2-normal-400-clean-20260815-v1',
    'stage': 'pp-revision-A-20260909-resume8-v1',
    'factorial': 'pp-revision-B-20260909-resume8-v1',
}
NORMAL_RESULTS = ROOT / 'experiments/pp/v2/normal/results' / RUNS['normal']
SECRET = re.compile(r'(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{25,}|gh[pousr]_[A-Za-z0-9]{25,})')
EXCLUDE_PARTS = {'__pycache__', '.git', '.venv', 'control', 'stage0-qualification'}
EXCLUDE_NAMES = {'.env', '.lock', '.pipeline-lock', '.request-lock', 'launcher.json', 'host-resources.jsonl', 'resources.jsonl', 'resource-monitor.log', 'continuation.log', 'source.patch'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def jsonl(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def sanitize(data):
    text = data.decode('utf-8')
    # Source paths are historical metadata, not dependencies of the release loader.
    text = re.sub(r'/home/[^/\s"\x27]+/aiwolf/aiwolf-nlp-agent-llm(?:-camera-ready)?', '.', text)
    text = re.sub(r'/home/[A-Za-z0-9_.-]+', '<HOME>', text)
    text = SECRET.sub('<REDACTED_CREDENTIAL>', text)
    return text.encode('utf-8')


def allowed(path):
    return not (set(path.parts) & EXCLUDE_PARTS or path.name in EXCLUDE_NAMES or path.name.startswith('.env') or path.suffix in {'.pyc', '.zip', '.pdf', '.svg'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dataset')
    args = parser.parse_args()
    dest = args.output.resolve()
    if not dest.is_relative_to(ROOT):
        raise ValueError('Export output must be inside this checkout')
    if (dest / 'raw').exists():
        raise FileExistsError('Refusing to overwrite an existing export')
    (dest / 'raw').mkdir(parents=True)
    transforms, members, exclusions = [], [], []
    for key, name in RUNS.items():
        source = ROOT / 'experiments/runs' / name
        if not source.is_dir() or source.is_symlink():
            raise ValueError(f'Missing local source: {name}')
        count = 0
        with (dest / 'raw' / f'{key}.tar.gz').open('wb') as stream:
            with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='', compresslevel=1) as zipped:
                with tarfile.open(fileobj=zipped, mode='w|') as archive:
                    for path in sorted(source.rglob('*')):
                        if path.is_symlink():
                            raise ValueError(f'Symlink in source: {path.relative_to(ROOT)}')
                        if not path.is_file():
                            continue
                        relative = path.relative_to(source)
                        if not allowed(relative):
                            exclusions.append({'run': key, 'path': relative.as_posix(), 'reason': 'local runtime, cache, excluded qualification, or derived binary/figure'})
                            continue
                        before = path.read_bytes()
                        try:
                            after = sanitize(before)
                        except UnicodeDecodeError:
                            exclusions.append({'run': key, 'path': relative.as_posix(), 'reason': 'non-text binary'})
                            continue
                        if key == 'normal' and relative.as_posix() == 'execution.jsonl':
                            rows = [json.loads(s) for s in after.decode().splitlines() if s.strip()]
                            for row in rows:
                                if row.get('server_json'):
                                    value = row['server_json']
                                    marker = '/server-logs/'
                                    if marker not in value:
                                        raise ValueError('Unrecognized normal game path')
                                    row['server_json'] = 'server-logs/' + value.split(marker, 1)[1]
                            after = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows).encode()
                        info = tarfile.TarInfo(relative.as_posix())
                        info.size, info.mode, info.mtime = len(after), 0o644, 0
                        archive.addfile(info, io.BytesIO(after))
                        entry = {'run': key, 'path': relative.as_posix(), 'source_sha256': digest(before), 'sha256': digest(after), 'bytes': len(after)}
                        members.append(entry)
                        if before != after:
                            transforms.append({**entry, 'operation': 'local-path normalization / credential redaction; normal server_json made run-relative'})
                        count += 1
        print(f'{key}: {count} archived files', flush=True)
    # Small typed views for Dataset Hub; the archives retain the complete evidence.
    def view(name, rows):
        path = dest / 'data' / f'{name}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows))
        return len(rows)
    fixed = ROOT / 'experiments/runs' / RUNS['fixed']
    stage = ROOT / 'experiments/runs' / RUNS['stage']
    factorial = ROOT / 'experiments/runs' / RUNS['factorial']
    boards = ROOT / 'experiments/pp/fixed_boards_v2/generated'
    for name in ['inputs.jsonl', 'labels.jsonl']:
        path = dest / 'boards' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((boards / name).read_bytes())
    labels = {r['scenario_id']: r for r in jsonl(boards / 'labels.jsonl')}
    counts = {'boards': view('boards', [{'scenario_id': r['scenario_id'], 'input_json': json.dumps(r, ensure_ascii=False, sort_keys=True), 'label_json': json.dumps(labels[r['scenario_id']], ensure_ascii=False, sort_keys=True)} for r in jsonl(boards / 'inputs.jsonl')])}
    for key, path in [('fixed_units', fixed), ('stage_units', stage)]:
        rows = []
        for p in sorted((path / 'units').glob('*.json')):
            r = read(p)
            rows.append({k: r[k] for k in ['unit_id', 'scenario_id', 'condition', 'repetition', 'game_id', 'talk', 'vote']} | {'group_id': r.get('group_id'), 'cluster': r['scenario_id'].replace('-POS-', '-').replace('-WER-', '-')})
        counts[key] = view(key, rows)
    with (NORMAL_RESULTS / 'games.csv').open() as f:
        rows = list(csv.DictReader(f))
    counts['normal_games'] = view('normal_games', [{'row_id': r['row_id'], 'pair_id': r['match_pair_id'], 'condition': r['condition'], 'game_id': r['game_id'], 'won': r['won'] == 'True', 'details_json': json.dumps(r, sort_keys=True)} for r in rows])
    counts['factorial_games'] = view('factorial_games', read(factorial / 'analysis/analysis.json')['games'])
    judgments = []
    for key, base in [('fixed', ROOT / 'experiments/runs' / RUNS['fixed_judge'] / 'results'), ('stage', stage / 'judge')]:
        for p in sorted(base.glob('*.json') if key == 'fixed' else base.glob('*/R[123].json')):
            r = read(p)
            if 'result' in r and 'success' in r:
                judgments.append({'experiment': key, 'talk_id': r.get('blind_id', p.parent.name), 'repetition': r.get('judge_repetition', int(p.stem[1:]) if re.fullmatch(r'R[123]', p.stem) else None), 'success': r['success'], 'judgment_json': json.dumps(r, ensure_ascii=False, sort_keys=True)})
    counts['talk_judgments'] = view('talk_judgments', judgments)
    references = {'fixed': fixed / 'analysis-v1/analysis.json', 'normal': NORMAL_RESULTS / 'analysis.json', 'stage': stage / 'analysis/analysis.json', 'factorial': factorial / 'analysis/analysis.json', 'cue': fixed / 'analysis-v1/cue-strata-audit.json', 'partner': NORMAL_RESULTS / 'partner-protocol-audit.json'}
    for key, path in references.items():
        out = dest / 'results' / f'{key}.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(sanitize(path.read_bytes()))
    attempts = []
    for start in sorted((factorial / 'attempts').glob('*/*/start.json')):
        result_path = start.parent / 'result.json'
        result = read(result_path) if result_path.exists() else {'status': 'missing'}
        attempts.append({'row_id': start.parents[1].name, 'attempt': start.parent.name, 'status': result['status'], 'result_json': json.dumps(json.loads(sanitize(json.dumps(result).encode())), sort_keys=True)})
    with (dest / 'provenance/attempts.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=['row_id', 'attempt', 'status', 'result_json'])
        writer.writeheader(); writer.writerows(attempts)
    expected = {'boards': 120, 'fixed_units': 480, 'stage_units': 960, 'normal_games': 400, 'factorial_games': 800, 'talk_judgments': 2880}
    if counts != expected or len(attempts) != 820:
        raise ValueError(f'Unexpected counts: {counts}, attempts={len(attempts)}')
    write(dest / 'provenance/release-manifest.json', {'schema_version': 1, 'status': 'local release candidate; unpublished', 'runs': RUNS, 'counts': counts, 'members': members, 'excluded': exclusions})
    (dest / 'provenance/transformations.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in transforms))
    print(f'Export counts: {counts}; attempts={len(attempts)}', flush=True)


if __name__ == '__main__':
    main()
