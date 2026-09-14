"""Correction releases retain input identity and fail before using incompatible data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from experiments.pp.fixed_boards.scoring_rules import SCORING_RULES_VERSION
from release.import_correction import build, validate_correction
from release.reproduce import ROOT, sha256, verify_correction_version, verify_dataset


class CorrectionReleaseTests(TestCase):
    def test_old_or_unknown_dataset_cannot_use_corrected_scorer(self):
        for manifest in ({}, {'scoring_correction': {'scoring_rules_version': 'old'}}):
            with self.assertRaisesRegex(ValueError, 'matching corrected dataset'):
                verify_correction_version(manifest)
        verify_correction_version({'scoring_correction': {'scoring_rules_version': SCORING_RULES_VERSION}})

    def test_dataset_rejects_tampered_reference(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            result = root / 'result.json'
            result.write_text('{"n": 80}')
            (root / 'checksums.sha256').write_text(f'{sha256(result)}  result.json\n')
            verify_dataset(root)
            result.write_text('{"n": 79}')
            with self.assertRaisesRegex(ValueError, 'Integrity mismatch'):
                verify_dataset(root)

    def test_dataset_rejects_unrecorded_files_and_duplicate_checksums(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            result = root / 'result.json'
            result.write_text('{}')
            entry = f'{sha256(result)}  result.json\n'
            (root / 'checksums.sha256').write_text(entry + entry)
            with self.assertRaisesRegex(ValueError, 'Duplicate checksum'):
                verify_dataset(root)
            (root / 'checksums.sha256').write_text(entry)
            (root / 'extra.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
                verify_dataset(root)

    def test_import_compares_original_source_hash_not_exported_hash(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            data, source = root / 'data', root / 'correction'
            (data / 'provenance').mkdir(parents=True)
            source.mkdir()
            base = {'runs': {'fixed': 'run'}, 'members': [
                {'run': 'fixed', 'path': 'manifest.json', 'source_sha256': 'original', 'sha256': 'exported'}]}
            (data / 'provenance/release-manifest.json').write_text(json.dumps(base))
            rules = 'experiments/pp/fixed_boards/scoring_rules.py'
            formal = {'scoring_rules_version': SCORING_RULES_VERSION, 'api_calls': 0,
                      'original_data_modified': False, 'semantic_judgments_reused': True,
                      'files': {}, 'audit_files': {}, 'source_hashes': {rules: sha256(ROOT / rules)}}
            (source / 'manifest.json').write_text(json.dumps(formal))
            (source / 'input-manifest.json').write_text(json.dumps({'experiments/runs/run/manifest.json': 'exported'}))
            with patch('release.import_correction.verify_dataset'):
                with self.assertRaisesRegex(ValueError, 'differs from release source'):
                    validate_correction(data, source)

    def test_import_refuses_to_overwrite_existing_output(self):
        with TemporaryDirectory(dir=ROOT / '.verification') as folder:
            root = Path(folder)
            saved = root / 'keep.json'
            saved.write_text('{"kept": true}')
            with self.assertRaisesRegex(ValueError, 'new output directory'):
                build(ROOT / 'dataset', root / 'source', root, 'test-commit')
            self.assertEqual(saved.read_text(), '{"kept": true}')
