"""Single-instance lock (FR-103, C-1).

The lock is what keeps two containers from writing to one SQLite file. The tests cover in-process
contention, release on error, and a real second process exiting with the documented code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_auto_poll.app import (
    EXIT_FAILURE,
    EXIT_LOCKED,
    LockHeldError,
    SingleInstanceLock,
    main,
)
from telegram_auto_poll.config import load_settings
from tests.conftest import FAKE_BOT_TOKEN


def _settings(data_dir: Path) -> object:
    return load_settings(
        {
            "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
            "ADMIN_USER_IDS": "100000001",
            "DATA_DIR": str(data_dir),
        }
    )


def test_acquire_creates_a_private_lock_file_with_the_pid(tmp_path: Path) -> None:
    lock = SingleInstanceLock(tmp_path)
    lock.acquire()
    try:
        assert lock.path == tmp_path / "app.lock"
        assert lock.path.stat().st_mode & 0o777 == 0o600
        assert lock.path.read_text(encoding="ascii") == str(os.getpid())
    finally:
        lock.release()


def test_acquire_creates_missing_directories(tmp_path: Path) -> None:
    lock = SingleInstanceLock(tmp_path / "nested" / "data")
    lock.acquire()
    try:
        assert lock.path.exists()
    finally:
        lock.release()


def test_a_second_lock_is_refused(tmp_path: Path) -> None:
    first = SingleInstanceLock(tmp_path)
    second = SingleInstanceLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(LockHeldError, match="another instance"):
            second.acquire()
    finally:
        first.release()


def test_release_allows_a_new_owner(tmp_path: Path) -> None:
    first = SingleInstanceLock(tmp_path)
    second = SingleInstanceLock(tmp_path)
    first.acquire()
    first.release()

    second.acquire()
    try:
        assert second.path.read_text(encoding="ascii") == str(os.getpid())
    finally:
        second.release()


def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = SingleInstanceLock(tmp_path)
    lock.acquire()
    lock.release()
    lock.release()


def test_context_manager_releases_after_an_exception(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), SingleInstanceLock(tmp_path) as lock:
        assert lock.path.exists()
        raise RuntimeError("boom")

    owner = SingleInstanceLock(tmp_path)
    owner.acquire()
    owner.release()


def test_main_refuses_to_start_when_the_lock_is_held(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with SingleInstanceLock(tmp_path):
        assert main(_settings(tmp_path)) == EXIT_LOCKED  # type: ignore[arg-type]

    assert "refusing to start" in capsys.readouterr().err


def test_main_runs_and_releases_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(stop: object) -> None:
        return None

    monkeypatch.setattr(
        "telegram_auto_poll.app.build_runtime", lambda settings: SimpleNamespace(run=run)
    )

    assert main(_settings(tmp_path)) == 0  # type: ignore[arg-type]

    owner = SingleInstanceLock(tmp_path)
    owner.acquire()
    owner.release()


def test_main_reports_invalid_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    # The conftest fixture removes every application variable, so the token is missing.
    assert main() == EXIT_FAILURE
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_second_process_exits_with_the_locked_code(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
        "ADMIN_USER_IDS": "100000001",
        "DATA_DIR": str(tmp_path),
    }
    with SingleInstanceLock(tmp_path):
        completed = subprocess.run(
            [sys.executable, "-m", "telegram_auto_poll"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=60,
        )

    assert completed.returncode == EXIT_LOCKED
    assert "refusing to start" in completed.stderr
    assert not (tmp_path / "telegram-auto-poll.db").exists()
