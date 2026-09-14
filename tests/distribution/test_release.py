"""Integrity and relocation regressions for the offline distribution reader."""
import tempfile
from pathlib import Path
from unittest import TestCase

from release.reproduce import compare, safe_path, sha256, verify_files
from experiments.pp.v2.normal.analyze_results import saved_game_path


class ReleaseTests(TestCase):
    def test_game_paths_resolve_relative_to_supplied_run_and_reject_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(saved_game_path(root, 'server-logs/game.json'), root / 'server-logs/game.json')
            for value in ['../game.json', str(root.parent / 'game.json')]:
                with self.assertRaises(ValueError):
                    saved_game_path(root, value)

    def test_archive_paths_reject_traversal_and_external_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for value in ['../outside', '/outside', '']:
                with self.assertRaises(ValueError):
                    safe_path(root, value)
            (root / 'link').symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                safe_path(root, 'link/outside')

    def test_tampered_file_fails_before_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'sample.json'
            path.write_text('{"won": true}')
            expected = {'sample.json': sha256(path)}
            verify_files(root, expected)
            path.write_text('{"won": false}')
            with self.assertRaises(ValueError):
                verify_files(root, expected)

    def test_comparison_preserves_denominators_and_full_precision(self):
        compare({'rate': 0.6996875000000002}, {'rate': 0.6996875})
        for actual, expected in [({'n': 79}, {'n': 80}), ({'rate': 0.70}, {'rate': 0.6996875}), ([True], [1]), ({'rate': float('nan')}, {'rate': 0.5})]:
            with self.assertRaises(ValueError):
                compare(actual, expected)
