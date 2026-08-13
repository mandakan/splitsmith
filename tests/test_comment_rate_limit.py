"""Per-key comment rate limiting."""

from __future__ import annotations

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
