"""Tests for action-timeout worker thread lifecycle."""

# ruff: noqa: PT009

from __future__ import annotations

from unittest import TestCase

from utils.stoppable_thread import StoppableThread


class StoppableThreadTest(TestCase):
    """Keep leftover LLM timeout workers from pinning the process."""

    def test_timeout_workers_are_daemon_by_default(self) -> None:
        """Action-timeout threads must not block process exit after FINISH."""
        thread = StoppableThread(target=lambda: None)
        self.assertTrue(thread.daemon)
