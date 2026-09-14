"""Regression checks for immutable, offline correction of archived experiments."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from experiments.pp.revision.common import digest, write
from experiments.pp.revision.reanalyze import main, verify_archived_revision


class ArchivedReanalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / "original"
        self.run.mkdir()
        source = self.run / "source/example.py"
        source.parent.mkdir()
        source.write_text("# historical source, not the current implementation\n")
        write(self.run / "units/one.json", {"output": "Alice"})
        state = {"schedule_finished": True, "started": ["one"]}
        write(self.run / "state.json", state)
        write(self.run / "manifest.json", {"files": {"source/example.py": digest(source)},
              "source_hashes": {"example.py": digest(source)}})
        write(self.run / "frozen.json", {"manifest_sha256": digest(self.run / "manifest.json"),
              "state": state, "evidence": {"units/one.json": digest(self.run / "units/one.json")}})

    def test_saved_source_is_checked_without_current_source_or_server(self):
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            _, frozen = verify_archived_revision(self.run)
        self.assertTrue(frozen["state"]["schedule_finished"])

    def test_tampered_source_is_rejected(self):
        (self.run / "source/example.py").write_text("# replacement scorer is not historical source\n")
        with self.assertRaisesRegex(ValueError, "Archived artifact changed"):
            verify_archived_revision(self.run)

    def test_changed_output_and_extra_evidence_are_rejected(self):
        write(self.run / "units/extra.json", {"output": "Bob"})
        with self.assertRaisesRegex(ValueError, "evidence set changed"):
            verify_archived_revision(self.run)
        (self.run / "units/extra.json").unlink()
        write(self.run / "units/one.json", {"output": "Bob"})
        with self.assertRaisesRegex(ValueError, "Archived artifact changed"):
            verify_archived_revision(self.run)

    def test_operational_state_must_remain_frozen(self):
        write(self.run / "state.json", {"schedule_finished": False})
        with self.assertRaisesRegex(ValueError, "operational state"):
            verify_archived_revision(self.run)

    def test_correction_cannot_overwrite_existing_results(self):
        output = Path(self.tmp.name) / "correction"
        saved = output / "A/analysis.json"
        write(saved, {"kept": True})
        old_hash = digest(saved)
        with patch("sys.argv", ["reanalyze", "--output", str(output)]):
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                main()
        self.assertEqual(digest(saved), old_hash)


if __name__ == "__main__":
    unittest.main()
