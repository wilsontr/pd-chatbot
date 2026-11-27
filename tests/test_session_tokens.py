"""TASK-5.29: session ids the server issues and can check."""

from __future__ import annotations

import pytest

from agent import session_tokens
from agent.session_tokens import is_valid_session_id, mint_session_id


@pytest.fixture(autouse=True)
def fresh_secrets(monkeypatch):
    session_tokens.reset_secrets()
    yield monkeypatch
    session_tokens.reset_secrets()


def _with_secret(monkeypatch, value: str) -> None:
    monkeypatch.setenv("SESSION_SECRET", value)
    session_tokens.reset_secrets()


def test_a_minted_id_verifies_and_each_one_is_new(monkeypatch):
    _with_secret(monkeypatch, "s3cret")
    a, b = mint_session_id(), mint_session_id()
    assert a != b
    assert is_valid_session_id(a) and is_valid_session_id(b)
    assert len(a) <= 128  # fits the API's session_id field


@pytest.mark.parametrize("value", [None, "", "s1", "web-session-42", "0" * 32 + "." + "0" * 64])
def test_ids_the_server_did_not_mint_are_refused(monkeypatch, value):
    _with_secret(monkeypatch, "s3cret")
    assert not is_valid_session_id(value)


def test_a_tampered_id_is_refused(monkeypatch):
    _with_secret(monkeypatch, "s3cret")
    nonce, sig = mint_session_id().split(".")
    other = ("1" if nonce[0] != "1" else "2") + nonce[1:]
    assert not is_valid_session_id(f"{other}.{sig}")


def test_an_id_from_another_secret_is_refused(monkeypatch):
    _with_secret(monkeypatch, "theirs")
    theirs = mint_session_id()
    _with_secret(monkeypatch, "ours")
    assert not is_valid_session_id(theirs)


def test_rotation_keeps_old_sessions_and_signs_new_ones_with_the_first(monkeypatch):
    _with_secret(monkeypatch, "old")
    old_session = mint_session_id()
    _with_secret(monkeypatch, "new,old")
    new_session = mint_session_id()
    assert is_valid_session_id(old_session)
    _with_secret(monkeypatch, "new")
    assert is_valid_session_id(new_session) and not is_valid_session_id(old_session)


def test_without_a_secret_ids_still_verify_within_the_process(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    session_tokens.reset_secrets()
    assert is_valid_session_id(mint_session_id())
