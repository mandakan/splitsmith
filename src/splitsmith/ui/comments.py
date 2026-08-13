"""Request/response models and pure helpers for timestamped comments.

The route handlers live in ``server.py`` next to the other share routes
(this codebase declares routes inline; #680 tracks the router split).
What lives here is everything that can be tested without an app: the
request model whose *absent* fields are load-bearing, the clamping rule,
and the response projection.
"""

from __future__ import annotations

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
# limit which bounds speed rather than total.
STAGE_COMMENT_CAP: Final = 500

# Same bound the frontend's parseMoment enforces, and the same one
# share_og.py clamps a moment card to.
T_LIMIT: Final = 3600.0


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
    id: str
    anchor_t: float
    anchor_kind: str
    anchor_shot_id: str | None
    author_kind: str
    author_handle: str
    body: str
    created_at: datetime
    mine: bool
    # Owner view only - the two bulk-moderation actions need them. Absent
    # (None, excluded on serialization) for anonymous callers.
    share_token_id: str | None = None
    author_key_hash: str | None = None


class CommentListResponse(BaseModel):
    comments: list[CommentOut]


def to_out(comment: Comment, *, author_key_hash: str | None, owner_view: bool) -> CommentOut:
    """Project a stored comment for the wire.

    ``author_key_hash`` is the *caller's*, used only to compute ``mine``;
    a caller who sent no key gets ``mine=False`` everywhere, which is the
    correct answer for a first-time reader.
    """
    return CommentOut(
        id=comment.id,
        anchor_t=comment.anchor_t,
        anchor_kind=comment.anchor_kind,
        anchor_shot_id=comment.anchor_shot_id,
        author_kind=comment.author_kind,
        author_handle=comment.author_handle,
        body=comment.body,
        created_at=comment.created_at,
        mine=author_key_hash is not None and comment.author_key_hash == author_key_hash,
        share_token_id=comment.share_token_id if owner_view else None,
        author_key_hash=comment.author_key_hash if owner_view else None,
    )
