"""Tests for isolated concurrent PP pilot runtime configuration."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import yaml

from experiments.runtime.run_one import materialize_runtime_configs


class PPParallelRuntimeTest(TestCase):
    """Verify that concurrent rows cannot share ports or writable log paths."""

    def test_runtime_configs_isolate_port_and_logs_by_row(self) -> None:
        """Derived configs retain behavior while isolating transport and artifacts."""
        row = {"row_id": "PP01", "condition": "P+"}
        with TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            (run / "agent-configs").mkdir(parents=True)
            (run / "server-configs").mkdir()
            (run / "opponent-configs").mkdir()
            (run / "optimizer-seeds").mkdir()
            server = {
                "server": {"web_socket": {"host": "127.0.0.1", "port": 8080}},
                "matching": {"output_path": "frozen"},
            }
            client = {
                "web_socket": {"url": "ws://127.0.0.1:8080/ws"},
                "agent": {"team": "yatolab"},
                "log": {"output_dir": "shared"},
                "experiment": {"condition": "P+"},
            }
            (run / "server-configs" / "PP01.yml").write_text(yaml.safe_dump(server), encoding="utf-8")
            (run / "agent-configs" / "P+.yml").write_text(yaml.safe_dump(client), encoding="utf-8")
            (run / "optimizer-seeds" / "PP01.json").write_text('{"scheduled_matches": []}\n', encoding="utf-8")
            for team in ("opponentOalpha", "opponentObravo", "opponentOcharlie", "opponentOdelta"):
                opponent = {**client, "agent": {"team": team}, "experiment": {"opponent_id": "O"}}
                (run / "opponent-configs" / f"{team}.yml").write_text(
                    yaml.safe_dump(opponent),
                    encoding="utf-8",
                )

            server_path, target_path, opponent_paths = materialize_runtime_configs(run, row, 18080)
            server = yaml.safe_load(server_path.read_text(encoding="utf-8"))
            target = yaml.safe_load(target_path.read_text(encoding="utf-8"))
            opponents = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in opponent_paths]

            self.assertEqual(server["server"]["web_socket"]["port"], 18080)
            self.assertTrue(server["matching"]["output_path"].endswith("/runtime-configs/PP01/default/optimizer.json"))
            self.assertEqual(
                (run / "runtime-configs" / "PP01" / "default" / "optimizer.json").read_text(encoding="utf-8"),
                '{"scheduled_matches": []}\n',
            )
            self.assertEqual(target["web_socket"]["url"], "ws://127.0.0.1:18080/ws")
            self.assertTrue(target["log"]["output_dir"].endswith(f"/P+/{row['row_id']}/default"))
            self.assertEqual(len({item["log"]["output_dir"] for item in opponents}), 4)
            self.assertTrue(all(item["web_socket"]["url"].endswith(":18080/ws") for item in opponents))
            self.assertTrue(all(row["row_id"] in item["log"]["output_dir"] for item in opponents))
