"""Tests for the environment-parsing helpers used by ``config.settings``."""

from __future__ import annotations

import pytest

from config.settings import env_bool, env_list, env_origins


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("no", False), ("", False), ("maybe", False),
    ],
)
def test_env_bool_reads_common_spellings(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("SOME_FLAG", raw)
    assert env_bool("SOME_FLAG", default=not expected) is expected


def test_env_bool_falls_back_to_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert env_bool("SOME_FLAG", default=True) is True


def test_env_list_splits_and_strips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_LIST", " a , b ,, c ")
    assert env_list("SOME_LIST", "") == ["a", "b", "c"]


def test_env_origins_drops_trailing_slashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL copied from a browser ends in "/", which django-cors-headers rejects."""
    monkeypatch.setenv("SOME_ORIGINS", "https://app.vercel.app/, http://localhost:5173")

    assert env_origins("SOME_ORIGINS", "") == ["https://app.vercel.app", "http://localhost:5173"]


def test_env_origins_defaults_to_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_ORIGINS", raising=False)
    assert env_origins("SOME_ORIGINS", "") == []
