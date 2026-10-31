"""Architectural invariants that must hold for every commit (constitution §13.3, NFR-105).

These tests read the source tree instead of executing it; they fail as soon as a layering rule or a
patching convention is broken, without waiting for a reviewer to notice.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "telegram_auto_poll"
DOMAIN = SRC / "domain"

FORBIDDEN_IN_DOMAIN = {
    "sqlalchemy",
    "telegram",
    "apscheduler",
    "aiohttp",
    "prometheus_client",
    "pydantic",
    "pydantic_settings",
    "alembic",
}


def domain_modules() -> list[Path]:
    return sorted(DOMAIN.glob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_domain_package_exists() -> None:
    assert domain_modules(), "the domain package must exist after T-007"


def test_domain_imports_only_the_standard_library() -> None:
    offenders: dict[str, set[str]] = {}
    for module in domain_modules():
        bad = imported_roots(module) & FORBIDDEN_IN_DOMAIN
        if bad:
            offenders[module.name] = bad
    assert offenders == {}


def test_datetime_now_only_appears_in_the_clock_port() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if ("datetime.now(" in text or "datetime.utcnow(" in text) and path.name != "clock.py":
            offenders.append(path.relative_to(SRC).as_posix())
    assert offenders == []


def test_pragma_no_cover_is_limited_to_allowed_cases() -> None:
    allowed_files = {"__main__.py"}
    offenders = []
    for path in SRC.rglob("*.py"):
        if "db/migrations/versions/" in path.as_posix():
            continue
        if "# pragma: no cover" not in path.read_text(encoding="utf-8"):
            continue
        if path.name not in allowed_files:
            offenders.append(path.relative_to(SRC).as_posix())
    assert offenders == []


def test_network_access_is_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="network access is blocked"):
        socket.create_connection(("example.com", 80))

    probe = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="network access is blocked"):
            probe.connect(("203.0.113.1", 80))
    finally:
        probe.close()
