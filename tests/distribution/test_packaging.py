"""Regression checks for the actual ZIP boundary and its integrity report."""
import json
from pathlib import Path
import tempfile
from unittest import TestCase
import zipfile

from release.audit import inspect_distribution
from release.package import ARCHIVE_NAME, INVENTORY, MANIFEST, create_package, digest, paths
from release.seal import seal_code


class PackagingTests(TestCase):
    def setUp(self):
        # Fixtures stay inside the checkout and never use real credentials.
        parent = Path(__file__).resolve().parents[2] / '.verification'
        parent.mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.names = {INVENTORY, MANIFEST, 'README.md', 'src/main.py',
                      'docs/verification.json', 'config/.env.example'}
        for name in self.names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}\n' if path.suffix == '.json' else 'public fixture\n')
        (self.root / 'dataset').mkdir()
        (self.root / 'dataset/README.md').write_text('synthetic data fixture\n')
        self.write_inventory()

    def write_inventory(self):
        (self.root / INVENTORY).write_text(''.join(n + '\n' for n in sorted(self.names)))

    def package(self):
        seal_code(self.root)
        return create_package(self.root)

    def test_unlisted_secrets_backups_and_caches_never_enter_zip(self):
        for name in ['config/.env.local', 'config/.env.production', 'config/local-backup.yml',
                     'docs/notes.md.bak', 'src/.history/main.py', 'src/__pycache__/main.pyc']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic private marker\n')
        archive = self.package()
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(set(zipped.namelist()), self.names)
        manifest = json.loads((self.root / MANIFEST).read_text())
        self.assertEqual(set(manifest), self.names - {MANIFEST, 'docs/verification.json'})
        self.assertEqual(inspect_distribution(self.root)['status'], 'passed')

    def test_inventory_cannot_opt_in_secret_files_or_top_level_symlinks(self):
        for name in ['config/.env.local', 'config/secrets.yml.bak', 'src/.history/main.py']:
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic private marker\n')
                self.names.add(name)
                self.write_inventory()
                with self.assertRaises(ValueError):
                    list(paths(self.root))
                self.names.remove(name)
        self.write_inventory()
        readme = self.root / 'README.md'
        readme.unlink()
        readme.symlink_to(self.root / 'src/main.py')
        with self.assertRaises(ValueError):
            self.package()

    def test_package_refuses_changes_after_sealing(self):
        seal_code(self.root)
        (self.root / 'src/main.py').write_text('changed after sealing\n')
        with self.assertRaises(ValueError):
            create_package(self.root)
        self.assertFalse((self.root / 'dist' / ARCHIVE_NAME).exists())

    def test_actual_zip_and_checksum_are_verified(self):
        archive = self.package()
        checksum = self.root / 'dist/checksums.sha256'
        self.assertEqual(checksum.read_text(), f'{digest(archive)}  dist/{ARCHIVE_NAME}\n')
        self.assertEqual(inspect_distribution(self.root)['status'], 'passed')
        checksum.write_text('0' * 64 + f'  dist/{ARCHIVE_NAME}\n')
        result = inspect_distribution(self.root)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any(f['path'] == 'dist/checksums.sha256' for f in result['findings']))

    def test_changed_zip_is_rejected_even_with_updated_checksum(self):
        archive = self.package()
        with zipfile.ZipFile(archive) as zipped:
            contents = {n: zipped.read(n) for n in zipped.namelist()}
        contents['src/main.py'] = b'changed in ZIP only\n'
        with zipfile.ZipFile(archive, 'w') as zipped:
            for name, payload in contents.items():
                zipped.writestr(name, payload)
        (self.root / 'dist/checksums.sha256').write_text(f'{digest(archive)}  dist/{ARCHIVE_NAME}\n')
        result = inspect_distribution(self.root)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('ZIP member differs' in reason
                            for f in result['findings'] for reason in f['reasons']))

    def test_corrupt_zip_fails_without_overwriting_packaged_reports(self):
        archive = self.package()
        report = self.root / 'docs/verification.json'
        before = report.read_bytes()
        archive.write_bytes(b'not a zip archive\n')
        result = inspect_distribution(self.root)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('corrupt ZIP' in reason
                            for f in result['findings'] for reason in f['reasons']))
        self.assertEqual(report.read_bytes(), before)

    def test_zip_secret_is_scanned_without_printing_its_value(self):
        archive = self.package()
        secret = 'sk-' + 'a' * 32
        with zipfile.ZipFile(archive, 'a') as zipped:
            zipped.writestr('config/.env.local', secret)
        result = inspect_distribution(self.root)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('credential pattern' in f['reasons'] for f in result['findings']))
        self.assertNotIn(secret, json.dumps(result))
