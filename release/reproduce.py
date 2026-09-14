#!/usr/bin/env python3
"""Verify and re-score the distributed evidence, without network or game servers."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.pp.fixed_boards import score_analysis as fixed_score
from experiments.pp.revision import analyze as revision
from experiments.pp.revision import reanalyze as correction
from experiments.pp.fixed_boards.scoring_rules import SCORING_RULES_VERSION
from experiments.pp.v2 import fixed_board_cue_audit as cue
from experiments.pp.v2.normal import analyze_partner_protocol as partner


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, relative):
    p = PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError(f'Unsafe relative path: {relative}')
    path = root.joinpath(*p.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes root: {relative}')
    return path


def verify_files(root, entries):
    for relative, expected in entries.items():
        path = safe_path(root, relative)
        if path.is_symlink() or sha256(path) != expected:
            raise ValueError(f'Integrity mismatch: {relative}')


def verify_dataset(data):
    checksums = {}
    for line in (data / 'checksums.sha256').read_text().splitlines():
        digest, relative = line.split('  ', 1)
        if relative in checksums:
            raise ValueError('Duplicate checksum entry')
        checksums[relative] = digest
    actual = {p.relative_to(data).as_posix() for p in data.rglob('*') if p.is_file()
              and p.name != 'checksums.sha256' and '.cache' not in p.parts and '.git' not in p.parts}
    if actual != set(checksums):
        raise ValueError('Dataset file inventory mismatch')
    verify_files(data, checksums)


def verify_correction_version(manifest):
    if manifest.get('scoring_correction', {}).get('scoring_rules_version') != SCORING_RULES_VERSION:
        raise ValueError('Use the matching corrected dataset release (english-declarations-v2-20260910)')


def add_execution_diagnostic(result, scenarios):
    components = ('correct_co_or_response', 'ally_preserved', 'valid_target_selected', 'talk_vote_consistent')
    result['deterministic_execution'] = {
        condition: correction.proportion(sum(all(row['component_both_repetitions'][key] for key in components)
                                             for row in scenarios if row['condition'] == condition
                                             and row['scenario_class'] == 'established_pp'), 80)
        for condition in fixed_score.CONDITIONS}


def unpack(data, work):
    manifest = read(data / 'provenance/release-manifest.json')
    expected = defaultdict(dict)
    for row in manifest['members']:
        if row['path'] in expected[row['run']]:
            raise ValueError('Duplicate manifest member')
        expected[row['run']][row['path']] = row['sha256']
    for run in manifest['runs']:
        seen = set()
        with tarfile.open(data / 'raw' / f'{run}.tar.gz', 'r|gz') as archive:
            for member in archive:
                if not member.isfile() or member.name in seen or member.name not in expected[run]:
                    raise ValueError('Unexpected archive member')
                path = safe_path(work / run, member.name)
                payload = archive.extractfile(member).read()
                if hashlib.sha256(payload).hexdigest() != expected[run][member.name]:
                    raise ValueError(f'Archive member hash mismatch: {run}/{member.name}')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                seen.add(member.name)
        if seen != set(expected[run]):
            raise ValueError(f'Incomplete archive: {run}')
        print(f'Verified and extracted {run}: {len(seen)} files', flush=True)
    return manifest


def compare(actual, expected, path=''):
    """Compare full precision, retaining every result field and list order."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError(f'Result keys differ at {path}')
        for key in expected:
            compare(actual[key], expected[key], f'{path}/{key}')
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError(f'Result length differs at {path}')
        for i, (a, e) in enumerate(zip(actual, expected)):
            compare(a, e, f'{path}/{i}')
    elif isinstance(expected, float):
        if not isinstance(actual, (int, float)) or not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f'Numeric mismatch at {path}: {actual} != {expected}')
    elif type(actual) is not type(expected) or actual != expected:
        raise ValueError(f'Result mismatch at {path}: {actual!r} != {expected!r}')


def comparable(value):
    # These top-level fields identify original paths/code/serialization, not outcomes.
    return {k: v for k, v in value.items() if k not in {'files', 'provenance', 'frozen_sha256', 'purpose', 'run_id'}}


def command(*args):
    subprocess.run([sys.executable, '-B', *map(str, args)], cwd=ROOT, check=True)


def fixed_judgments(judge, target):
    grouped = defaultdict(list)
    for path in sorted((judge / 'results').glob('*.json')):
        row = read(path)
        grouped[row['blind_id']].append(row)
    rows = []
    for blind, records in sorted(grouped.items()):
        if len(records) != 3 or {r['judge_repetition'] for r in records} != {1, 2, 3} or any(not r['success'] for r in records):
            raise ValueError('Three successful, distinct fixed judgment repetitions required')
        votes = {key: [bool(r['result'][key]['label']) for r in records] for key in fixed_score.SEMANTIC_FIELDS}
        rows.append({'blind_id': blind, 'labels': {k: sum(v) >= 2 for k, v in votes.items()}, 'judge_votes': votes})
    if len(rows) != 480:
        raise ValueError('Expected 480 judged units')
    fixed_score.write_jsonl(target, rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data, output = args.data.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(data) or data.is_relative_to(output):
        raise ValueError('Use a new output directory outside the dataset')
    verify_files(ROOT, read(ROOT / 'release/code-manifest.json'))
    verify_dataset(data)
    verify_correction_version(read(data / 'provenance/release-manifest.json'))
    output.mkdir(parents=True)
    work = output / 'evidence'
    manifest = unpack(data, work)
    semantics = output / 'fixed-semantic-scores.jsonl'
    votes = fixed_judgments(work / 'fixed_judge', semantics)
    compare(votes, fixed_score.read_jsonl(work / 'fixed/judge-inputs-v1/semantic-scores.jsonl'), 'judge-majorities')
    command('-m', 'experiments.pp.fixed_boards.score_analysis', work / 'fixed', work / 'fixed/judge-inputs-v1', semantics, output / 'fixed', '--corpus-dir', data / 'boards')
    fixed_result = read(output / 'fixed/analysis.json')
    add_execution_diagnostic(fixed_result, fixed_score.read_jsonl(output / 'fixed/scenario-scores.jsonl'))
    write(output / 'fixed/analysis.json', fixed_result)
    command('-m', 'experiments.pp.v2.normal.analyze_results', work / 'normal', output / 'normal', '--fixed-analysis', output / 'fixed/analysis.json')
    for name, fn in [('stage', revision.analyze_a), ('factorial', revision.analyze_b)]:
        run = work / name
        result = fn(run, read(run / 'frozen.json'))
        if name == 'factorial' and (result['complete_blocks'] != 200 or sum(result['attempts'].values()) != 820):
            raise ValueError('B requires 200 complete blocks and 820 attempts')
        write(output / name / 'analysis.json', result)
    write(output / 'cue/analysis.json', cue.create_artifact(data / 'boards/inputs.jsonl', data / 'boards/labels.jsonl', output / 'fixed/scenario-scores.jsonl'))
    write(output / 'partner/analysis.json', partner.create_artifact(work / 'normal', output / 'normal/games.csv'))
    units = {p.stem: read(p) for p in (work / 'fixed/units').glob('*.json')}
    items = {row['scenario_id']: row for row in fixed_score.read_jsonl(data / 'boards/inputs.jsonl')}
    extractions = correction.ledger(work / 'stage', work / 'factorial', units, items)
    compare(extractions, fixed_score.read_jsonl(work / 'correction/extractions.jsonl'), 'correction-extractions')
    changed = [row for row in extractions if row['old'] != row['corrected']]
    compare(changed, fixed_score.read_jsonl(work / 'correction/changed-extractions.jsonl'), 'changed-extractions')
    fixed_score.write_jsonl(output / 'extractions.jsonl', extractions)
    fixed_score.write_jsonl(output / 'changed-extractions.jsonl', changed)
    compare(read(data / 'results/factorial.json')['games'], fixed_score.read_jsonl(data / 'data/factorial_games.jsonl'), 'factorial-view')
    checks = {}
    for key in ['fixed', 'normal', 'stage', 'factorial', 'cue', 'partner']:
        compare(comparable(read(output / key / 'analysis.json')), comparable(read(data / 'results' / f'{key}.json')), key)
        checks[key] = 'matched: all non-provenance fields; float tolerance 1e-12'
        print(f'{key}: all results match', flush=True)
    f = read(output / 'fixed/analysis.json')
    n = read(output / 'normal/analysis.json')
    a = read(output / 'stage/analysis.json')
    b = read(output / 'factorial/analysis.json')
    def rate(v):
        return f"{v['numerator']}/{v['denominator']}"
    def effect(d, ci):
        return f"{100*d:+.1f} [{100*ci[0]:+.1f}, {100*ci[1]:+.1f}]"
    fixed_rates = f['outcome_summary']
    normal_rates = {r['condition']: r for r in n['primary']['condition_summary']}
    npair = n['primary']['paired_comparison']
    lines = ['# Reproduced camera-ready results', '', '| Evaluation | Outcome | Difference (95% CI), percentage points |', '| --- | --- | --- |']
    lines.append(f"| Original fixed | Composite {rate(fixed_rates['P+']['composite_pp_success'])} vs {rate(fixed_rates['P-PP']['composite_pp_success'])} | {effect(f['primary_comparison']['paired_risk_difference'], f['primary_comparison']['cluster_bootstrap95'])} |")
    lines.append(f"| Original normal | Wins {rate(normal_rates['P+'])} vs {rate(normal_rates['P-PP'])} | {effect(n['primary']['risk_difference_P+_minus_P-PP'], npair['match_pair_bootstrap95'])} |")
    lines.append(f"| A | Consistency {rate(a['rates']['A11']['consistent'])} vs {rate(a['rates']['A10']['consistent'])} | {effect(a['primary']['difference'], a['primary']['ci95'])} |")
    lines.append(f"| B | Wins {rate(b['rates_complete_blocks']['G11'])} vs {rate(b['rates_complete_blocks']['G00'])} | {effect(b['primary']['difference'], b['primary']['ci95'])} |")
    lines += ['', 'Computed from saved outputs and judgments; full-precision results and diagnostics are in the six analysis directories.', '']
    (output / 'tables.md').write_text('\n'.join(lines))
    write(output / 'verification.json', {'status': 'passed', 'checks': checks, 'counts': manifest['counts'], 'dataset_checksums_sha256': sha256(data / 'checksums.sha256'), 'code_manifest_sha256': sha256(ROOT / 'release/code-manifest.json'), 'network_required': False,
                                       'scoring_rules_version': SCORING_RULES_VERSION,
                                       'extraction_records_verified': len(extractions), 'changed_extraction_records_verified': len(changed)})
    print(f'PASS: {output / "verification.json"}', flush=True)


if __name__ == '__main__':
    main()
