"""One configured agent per OS process; no redundant multiprocessing wrappers."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from starter import connect


def main():
    config = yaml.safe_load(Path(sys.argv[1]).read_text())
    if config["agent"]["num"] != 1:
        raise ValueError("Revision client requires exactly one agent")
    connect(config, 1)


if __name__ == "__main__":
    main()
