"""``splitsmith.youtube.sealed``: the hosted refresh token at rest (issue
#1000, phase 2). Round trip under a key, a foreign key fails loud, and a
missing or malformed key is a configuration error, never a crash deeper
in."""

from __future__ import annotations

import pytest

from splitsmith.youtube import sealed
from splitsmith.youtube.oauth import NotConfiguredError


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    k = sealed.generate_key()
    monkeypatch.setenv(sealed.ENV_TOKEN_KEY, k)
    return k


def test_round_trip_under_the_env_key(key: str) -> None:
    token = "1//0refresh-token-with-unicode-å"
    boxed = sealed.seal(token)
    assert boxed != token
    assert token not in boxed
    assert sealed.open_sealed(boxed) == token


def test_explicit_key_wins_over_the_env(key: str) -> None:
    other = sealed.generate_key()
    boxed = sealed.seal("t", key=other)
    assert sealed.open_sealed(boxed, key=other) == "t"
    with pytest.raises(sealed.SealedTokenError, match="does not open"):
        sealed.open_sealed(boxed)


def test_a_rotated_key_does_not_open_old_rows(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    boxed = sealed.seal("t")
    monkeypatch.setenv(sealed.ENV_TOKEN_KEY, sealed.generate_key())
    with pytest.raises(sealed.SealedTokenError):
        sealed.open_sealed(boxed)


def test_tampered_ciphertext_fails(key: str) -> None:
    boxed = sealed.seal("t")
    flipped = boxed[:-2] + ("A" if boxed[-2] != "A" else "B") + boxed[-1]
    with pytest.raises(sealed.SealedTokenError):
        sealed.open_sealed(flipped)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_is_not_configured(value: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    if value is None:
        monkeypatch.delenv(sealed.ENV_TOKEN_KEY, raising=False)
    else:
        monkeypatch.setenv(sealed.ENV_TOKEN_KEY, value)
    assert sealed.is_key_configured() is False
    with pytest.raises(NotConfiguredError, match=sealed.ENV_TOKEN_KEY):
        sealed.seal("t")
    with pytest.raises(NotConfiguredError, match=sealed.ENV_TOKEN_KEY):
        sealed.open_sealed("anything")


def test_malformed_key_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sealed.ENV_TOKEN_KEY, "not-a-fernet-key")
    with pytest.raises(NotConfiguredError, match="not a Fernet key"):
        sealed.seal("t")


def test_generate_key_is_fresh_each_time() -> None:
    assert sealed.generate_key() != sealed.generate_key()
