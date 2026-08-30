"""Test isolation: no test run should ever write into the real production
event log or monitor-state file. Individual tests that need to observe
monitor_state already mock it explicitly (see test_monitor.py); this is the
backstop for log_event(), which every module calls internally regardless
of what else a given test has mocked."""

import pytest

import logging_setup
import monitor_state


@pytest.fixture(autouse=True)
def isolate_persisted_files(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(monitor_state, "STATE_PATH", tmp_path / "monitor_state.json")
