"""Script to launch agents according to configuration.

設定に応じたエージェントを起動するスクリプト.
"""

import argparse
import logging
import multiprocessing
from pathlib import Path
from typing import Any, cast

import yaml

from starter import connect

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


def get_manual_llm_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return manual LLM inspection config.

    Args:
        config (dict[str, Any]): Configuration dictionary.

    Returns:
        dict[str, Any]: Manual LLM inspection config.
    """
    debug_config = config.get("debug")
    if not isinstance(debug_config, dict):
        return {}
    debug_dict = cast("dict[object, object]", debug_config)
    manual_config = debug_dict.get("manual_llm")
    if not isinstance(manual_config, dict):
        return {}
    return cast("dict[str, Any]", manual_config)


def manual_llm_enabled(config: dict[str, Any]) -> bool:
    """Return whether manual LLM inspection is enabled.

    Args:
        config (dict[str, Any]): Configuration dictionary.

    Returns:
        bool: True when manual LLM inspection is enabled.
    """
    return bool(get_manual_llm_config(config).get("enabled", False))


def agent_name(config: dict[str, Any], idx: int) -> str:
    """Return configured client agent name for an index.

    Args:
        config (dict[str, Any]): Configuration dictionary.
        idx (int): Agent index.

    Returns:
        str: Client agent name.
    """
    return str(config["agent"]["team"]) + str(idx)


def normalize_manual_llm_agents(config: dict[str, Any], agent_num: int) -> set[str]:
    """Select the one agent that can use stdin for manual inspection.

    Args:
        config (dict[str, Any]): Configuration dictionary.
        agent_num (int): Number of agents.

    Returns:
        set[str]: Agent names targeted for manual inspection.
    """
    manual_config = get_manual_llm_config(config)
    configured_agents = [
        str(agent)
        for agent in manual_config.get("agents", [])
        if str(agent)
    ]
    available_agents = [agent_name(config, idx) for idx in range(1, agent_num + 1)]
    selected_agents = [agent for agent in configured_agents if agent in available_agents]
    if not selected_agents:
        selected_agents = [available_agents[0]]
        logger.info(
            "Setting the manual_llm target agent to %s",
            selected_agents[0],
        )
    if len(selected_agents) > 1:
        logger.warning(
            "manual_llm uses stdin, so restricting the target to %s only",
            selected_agents[0],
        )
        selected_agents = [selected_agents[0]]
    manual_config["agents"] = selected_agents
    return set(selected_agents)


def start_agent_process(
    config: dict[str, Any],
    idx: int,
    threads: list[multiprocessing.Process],
) -> None:
    """Start one agent in a child process.

    Args:
        config (dict[str, Any]): Configuration dictionary.
        idx (int): Agent index.
        threads (list[multiprocessing.Process]): Child process list.
    """
    thread = multiprocessing.Process(
        target=connect,
        args=(config, idx),
    )
    threads.append(thread)
    thread.start()


def execute_manual(config: dict[str, Any], agent_num: int) -> None:
    """Execute agents with one manual-inspection target in the parent process.

    Args:
        config (dict[str, Any]): Configuration dictionary.
        agent_num (int): Number of agents.
    """
    manual_agents = normalize_manual_llm_agents(config, agent_num)
    manual_idx = next(
        idx
        for idx in range(1, agent_num + 1)
        if agent_name(config, idx) in manual_agents
    )
    threads: list[multiprocessing.Process] = []
    for idx in range(1, agent_num + 1):
        if idx != manual_idx:
            start_agent_process(config, idx, threads)

    try:
        connect(config, manual_idx)
    except KeyboardInterrupt:
        for thread in threads:
            if thread.is_alive():
                thread.terminate()
        raise
    else:
        for thread in threads:
            thread.join()


def execute(config_path: Path) -> None:
    """Execute based on the configuration file.

    設定ファイルをもとに実行する.

    Args:
        config_path (Path): Path to the configuration file / 設定ファイルのパス
    """
    with Path.open(config_path) as f:
        config = yaml.safe_load(f)
        logger.info("Loaded the configuration file")

    agent_num = int(config["agent"]["num"])
    if manual_llm_enabled(config):
        execute_manual(config, agent_num)
        return

    threads: list[multiprocessing.Process] = []
    for i in range(agent_num):
        start_agent_process(config, i + 1, threads)
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        nargs="+",
        default=["./config/config.yml"],
        help="Path(s) to the configuration file (multiple allowed)",
    )
    args = parser.parse_args()

    paths: list[Path] = []
    for config_path in args.config:
        glob_path = Path(config_path)
        paths.extend([path for path in Path.glob(glob_path.parent, glob_path.name) if path.is_file()])

    multiprocessing.set_start_method("spawn")
    if len(paths) == 1:
        with Path.open(paths[0]) as f:
            config = yaml.safe_load(f)
        if manual_llm_enabled(config):
            execute(paths[0])
            raise SystemExit

    threads: list[multiprocessing.Process] = []
    for path in paths:
        thread = multiprocessing.Process(
            target=execute,
            args=(Path(path),),
        )
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
