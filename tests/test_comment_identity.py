"""Server-derived comment handles."""

from __future__ import annotations

from splitsmith.comment_identity import (
    ADJECTIVES,
    NOUNS,
    derive_handle,
    hash_author_key,
)


def test_handle_is_stable_for_a_key() -> None:
    secret = b"test-secret"
    assert derive_handle("abc123", secret=secret) == derive_handle("abc123", secret=secret)


def test_handle_differs_across_keys() -> None:
    secret = b"test-secret"
    handles = {derive_handle(f"key-{i}", secret=secret) for i in range(200)}
    # 200 draws from a ~102k space: collisions are possible but a large
    # cluster means the derivation is not spreading.
    assert len(handles) > 190


def test_handle_shape_is_adjective_noun_number() -> None:
    handle = derive_handle("abc123", secret=b"test-secret")
    adjective, noun, number = handle.split(" ")
    assert adjective in ADJECTIVES
    assert noun in NOUNS
    assert number.isdigit() and len(number) == 2


def test_handle_is_ascii_only() -> None:
    """CLAUDE.md: all user-visible copy is ASCII. A handle is copy."""
    for word in (*ADJECTIVES, *NOUNS):
        assert word.isascii()


def test_secret_changes_the_handle() -> None:
    """A rotated secret must not be reversible from an observed handle,
    so the mapping has to actually depend on it."""
    assert derive_handle("abc123", secret=b"one") != derive_handle("abc123", secret=b"two")


def test_hash_author_key_is_stable_and_not_the_key() -> None:
    assert hash_author_key("abc123") == hash_author_key("abc123")
    assert "abc123" not in hash_author_key("abc123")
    assert len(hash_author_key("abc123")) == 64
