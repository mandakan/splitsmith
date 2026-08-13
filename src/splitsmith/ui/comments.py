"""Request/response models and pure helpers for timestamped comments.

The route handlers live in ``server.py`` next to the other share routes
(this codebase declares routes inline; #680 tracks the router split).
What lives here is everything that can be tested without an app: the
request model whose *absent* fields are load-bearing, the clamping rule,
and the response projection.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    # Lazy at runtime (see ``server.py``'s own TYPE_CHECKING import block):
    # ``splitsmith.db`` pulls in sqlalchemy, which is only installed with
    # the ``hosted`` extra. ``from __future__ import annotations`` above
    # keeps every annotation in this module a string, so this import never
    # actually runs outside a type checker - a local-mode install that
    # imports ``splitsmith.ui.server`` (which imports this module
    # unconditionally) must not require sqlalchemy to be present.
    from ..db.comments import Comment

AUTHOR_KEY_HEADER: Final = "X-Splitsmith-Author-Key"

BODY_MAX_CHARS: Final = 1000
# Refuse further comments on a stage past this many. A blunt backstop
# against one link being used to fill a table, distinct from the rate
# limit which bounds speed rather than total. Bounding (slug, stage_number)
# themselves is what keeps this predicate meaningful - see
# ``_require_comment_scope`` in server.py; count_for_stage filters on the
# same two values so an unbounded slug/stage would reset the cap for free.
STAGE_COMMENT_CAP: Final = 500

# Same bound the frontend's parseMoment enforces, and the same one
# share_og.py clamps a moment card to.
T_LIMIT: Final = 3600.0

# C0 control characters (and DEL) are refused in a comment body (fix round
# 1, F6). A NUL byte in particular is not just cosmetic: SQLite happily
# stores it but Postgres's text type raises on the wire, so an anonymous
# POST that 201s in the dev/test SQLite backend 500s in production. Tab
# and newline are allowed - a multi-line comment is a normal thing to
# write; every other C0 code point and DEL are not something a client
# ever has a legitimate reason to send in prose.
_DISALLOWED_BODY_CHARS = frozenset(chr(c) for c in range(0x20) if c not in (0x09, 0x0A)) | {"\x7f"}


class CommentCreateRequest(BaseModel):
    """What an anonymous commenter may say.

    The fields that are NOT here are the point: ``author_handle``,
    ``author_kind``, ``author_user_id``, ``user_id``, ``match_id``,
    ``slug`` and ``stage_number`` are all server-side facts. pydantic
    ignores unknown keys by default, so a crafted body carrying them is
    silently dropped rather than rejected - which is what we want; a 422
    would tell a prober which names exist.
    """

    body: str = Field(min_length=1, max_length=BODY_MAX_CHARS)
    anchor_t: float
    anchor_kind: Literal["time", "shot"] = "time"
    anchor_shot_id: str | None = Field(default=None, max_length=128)

    @field_validator("body")
    @classmethod
    def _body_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("body must not be blank")
        return stripped

    @field_validator("body")
    @classmethod
    def _body_has_no_control_chars(cls, value: str) -> str:
        if any(ch in _DISALLOWED_BODY_CHARS for ch in value):
            raise ValueError("body must not contain control characters")
        return value

    @field_validator("anchor_t")
    @classmethod
    def _clamp_and_round(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("anchor_t must be finite")
        return round(max(-T_LIMIT, min(T_LIMIT, value)), 2)

    @model_validator(mode="after")
    def _shot_anchor_carries_a_shot_id(self) -> CommentCreateRequest:
        if self.anchor_kind == "shot" and not self.anchor_shot_id:
            raise ValueError("anchor_kind='shot' requires anchor_shot_id")
        if self.anchor_kind == "time" and self.anchor_shot_id:
            raise ValueError("anchor_shot_id is only valid with anchor_kind='shot'")
        return self


class CommentOut(BaseModel):
    """Anonymous-safe projection of a comment. No slot for
    ``share_token_id``/``author_key_hash`` at all - see
    :class:`CommentOwnerOut` for why that is the point (fix round 1, F4)."""

    id: str
    anchor_t: float
    anchor_kind: str
    anchor_shot_id: str | None
    author_kind: str
    author_handle: str
    body: str
    created_at: datetime
    mine: bool


class CommentOwnerOut(CommentOut):
    """Owner-view projection: adds the two fields Task 8's moderation
    routes need.

    A separate *type*, not ``CommentOut`` with two optional fields left
    ``None`` for anonymous callers. The earlier version did that plus
    ``response_model_exclude_none=True`` to hide the ``null``s - which
    made containment depend on a *value* (happens to be ``None`` today)
    rather than a *type* (this field does not exist on the anonymous
    model). The first owner-only field Task 8 adds with a non-``None``
    default would leak silently through that scheme while
    ``test_list_never_exposes_author_key_hash_or_share_token`` stayed
    green. It also suppressed ``anchor_shot_id: null`` for every
    time-anchored comment, contradicting the frontend contract that
    declares the field required ``string | null``.
    """

    share_token_id: str
    author_key_hash: str


class CommentListResponse(BaseModel):
    comments: list[CommentOut]


class CommentOwnerListResponse(BaseModel):
    comments: list[CommentOwnerOut]


def to_out(comment: Comment, *, author_key_hash: str | None, owner_view: bool) -> CommentOut:
    """Project a stored comment for the wire.

    ``author_key_hash`` is the *caller's*, used only to compute ``mine``;
    a caller who sent no key gets ``mine=False`` everywhere, which is the
    correct answer for a first-time reader. Returns a
    :class:`CommentOwnerOut` (a ``CommentOut`` subtype) when
    ``owner_view``, so a caller that forgets to branch on the type still
    gets every ``CommentOut`` field.

    **The ``owner_view`` branch here is belt-and-braces, not the primary
    defense.** The actual containment boundary is the response *type* at
    each call site: the anonymous list route wraps this in
    ``CommentListResponse`` (``comments: list[CommentOut]``), the POST
    route declares ``response_model=CommentOut`` - ``CommentOut`` itself
    has no ``author_key_hash`` / ``share_token_id`` fields to leak, so
    Pydantic strips them from any ``CommentOwnerOut`` instance handed to
    a ``CommentOut``-typed slot regardless of what this function does.
    Task 12's ablation drill confirmed this: removing the ``if
    owner_view`` gate here (always building ``CommentOwnerOut``) left
    every anonymous-exposure test green, because the type boundary at
    the call site caught it independently. Do not read that as "this
    branch is dead code, delete it" - keep it, because a future call
    site that returns ``to_out(...)`` directly without an intervening
    ``CommentOut``-typed wrapper would have nothing else standing
    between it and a leak. Do treat the *type* declarations at each call
    site (``CommentOut`` vs ``CommentOwnerOut``, `response_model=`) as
    the thing that must never regress; this flag alone was never enough.
    """
    mine = author_key_hash is not None and comment.author_key_hash == author_key_hash
    if owner_view:
        return CommentOwnerOut(
            id=comment.id,
            anchor_t=comment.anchor_t,
            anchor_kind=comment.anchor_kind,
            anchor_shot_id=comment.anchor_shot_id,
            author_kind=comment.author_kind,
            author_handle=comment.author_handle,
            body=comment.body,
            created_at=comment.created_at,
            mine=mine,
            share_token_id=comment.share_token_id,
            author_key_hash=comment.author_key_hash,
        )
    return CommentOut(
        id=comment.id,
        anchor_t=comment.anchor_t,
        anchor_kind=comment.anchor_kind,
        anchor_shot_id=comment.anchor_shot_id,
        author_kind=comment.author_kind,
        author_handle=comment.author_handle,
        body=comment.body,
        created_at=comment.created_at,
        mine=mine,
    )


class CommentRateLimiter:
    """Sliding-window comment limiter over one or more keys at once.

    **The share token id has to be one of the keys.** The first version
    keyed only on the hashed author key, and the author key is a header
    the client mints for itself: rotating it per request defeated the
    limiter completely - measured 8/8 accepted against 5/8 for a fixed
    key (final review, I5). The share token id is the link the caller
    holds; they cannot mint another one, so it is the bound that
    actually holds. Keying on the author key too is still worth it, so
    one visitor cannot spend the whole token's budget in a burst.

    In-process and per-replica by design. This is a spam speed bump, not
    a security control - the security properties are the scope gate and
    the allowlist. A shared counter would mean Redis, which is a new
    dependency, and the thing it would buy (exact limits across
    replicas) is not worth that on a personal tool's share surface.

    ``max_keys`` bounds the table so an attacker rotating author keys
    turns a spam control into a bigger table rather than a memory leak;
    the oldest entries are evicted first.
    """

    def __init__(self, *, limit: int = 5, window_s: float = 60.0, max_keys: int = 10_000) -> None:
        self._limit = limit
        self._window_s = window_s
        self._max_keys = max_keys
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, *keys: str, now: float) -> bool:
        """Consume one slot against every key, all or nothing.

        A request that any key refuses records a hit against none of
        them - otherwise a caller already over the token limit would go
        on burning the budget of every author key they rotate through.
        Namespace the keys at the call site (``token:``/``key:``) so two
        different kinds of identifier cannot collide in the table.
        """
        if not keys:
            raise ValueError("CommentRateLimiter.allow needs at least one key")
        # dict.fromkeys: de-duplicate without losing order, so passing
        # the same key twice does not count as two slots.
        pruned = {
            key: [t for t in self._hits.get(key, ()) if now - t < self._window_s]
            for key in dict.fromkeys(keys)
        }
        allowed = all(len(stamps) < self._limit for stamps in pruned.values())
        for key, stamps in pruned.items():
            if allowed:
                stamps.append(now)
            elif key not in self._hits:
                # A refused request must not seed an entry for a key
                # never seen before - that is the rotation the max_keys
                # bound exists to blunt, and there is nothing to record.
                continue
            self._hits[key] = stamps
            self._hits.move_to_end(key)
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
        return allowed

    def size(self) -> int:
        return len(self._hits)
