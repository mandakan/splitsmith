"""Comment rate limiting - per share token and per author key."""

from __future__ import annotations

import pytest

from splitsmith.ui.comments import CommentRateLimiter


def test_allows_up_to_the_limit_then_refuses() -> None:
    limiter = CommentRateLimiter(limit=3, window_s=60.0)
    assert [limiter.allow("k", now=0.0) for _ in range(3)] == [True, True, True]
    assert limiter.allow("k", now=0.0) is False


def test_window_slides() -> None:
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("k", now=0.0) is True
    assert limiter.allow("k", now=59.0) is False
    assert limiter.allow("k", now=61.0) is True


def test_keys_are_independent() -> None:
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("b", now=0.0) is True


def test_key_table_is_bounded() -> None:
    """An attacker rotating author keys must not grow the table without
    bound - that would turn a spam control into a memory leak."""
    limiter = CommentRateLimiter(limit=1, window_s=60.0, max_keys=10)
    for i in range(100):
        limiter.allow(f"key-{i}", now=float(i))
    assert limiter.size() <= 10


def test_all_keys_must_be_under_the_limit() -> None:
    """The multi-key form is the I5 fix: the call site passes both the
    share token id and the hashed author key, and either being over the
    limit refuses the request. Without this, rotating the (client-chosen)
    author key bought a fresh budget every time."""
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("token:t", "key:a", now=0.0) is True
    # Fresh author key, same token - the token's slot is spent.
    assert limiter.allow("token:t", "key:b", now=0.0) is False
    # Fresh token, spent author key - refused for the mirror reason.
    assert limiter.allow("token:u", "key:a", now=0.0) is False
    # Both fresh.
    assert limiter.allow("token:u", "key:b", now=0.0) is True


def test_a_refusal_spends_no_slot_on_the_other_keys() -> None:
    """All or nothing. If a refusal still recorded a hit against every
    key it named, a caller already over the token limit would burn the
    budget of every author key they rotated through, and those keys
    would stay refused after the token's window slid."""
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("token:t", "key:a", now=0.0) is True
    assert limiter.allow("token:t", "key:b", now=0.0) is False
    # key:b was never charged, so it is good once the token's window slides.
    assert limiter.allow("token:t", "key:b", now=61.0) is True


def test_a_refusal_does_not_seed_a_table_entry_for_an_unseen_key() -> None:
    """Refused requests must not grow the table - that is the rotation
    max_keys exists to blunt."""
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    limiter.allow("token:t", "key:a", now=0.0)
    before = limiter.size()
    for i in range(50):
        assert limiter.allow("token:t", f"key:rot-{i}", now=0.0) is False
    assert limiter.size() == before


def test_a_repeated_key_counts_once() -> None:
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("k", "k", now=0.0) is True
    assert limiter.allow("k", now=0.0) is False


def test_allow_requires_at_least_one_key() -> None:
    limiter = CommentRateLimiter()
    with pytest.raises(ValueError):
        limiter.allow(now=0.0)
