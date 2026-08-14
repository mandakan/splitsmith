"""Normalization and validation for an account's display name (#867).

``users.display_name`` is published under a public share link the moment
a signed-in visitor comments (#866), so what goes into the column is a
publishing decision, not a formatting preference. Three rules carry
weight:

**Blank becomes ``None``, never ``""``.** #866's attribution branch
falls back to a server-derived handle when the name is blank, and it
tests ``isinstance(str)`` *and* ``.strip()`` because it did not trust
the column to be clean. Storing ``None`` makes both guards agree and
keeps the fallback invariant true from the write side.

**NFC, not NFKC.** This preserves the name the user typed. The
comparison used to detect two authors with confusingly similar names is
a different function with different rules, and lives in the frontend
(``lib/authorAmbiguity.ts``) -- it folds compatibility forms because it
is trying to defeat someone choosing one on purpose. Do not reuse
either normalizer for the other's job.

**Control characters are refused outright,** except tab which is
collapsed to space. Tabs are the only control character a person plausibly
types into a text field; newlines, carriage returns, and C1/format codepoints
are rejected because they break single-line rendering or create invisible
padding that lets two visually identical names differ.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# Longest display name accepted, measured after normalizing so leading
# padding cannot consume the budget. Sized against the two surfaces that
# render it: the comment thread's author line and the account chip,
# which truncates at 16rem.
MAX_DISPLAY_NAME_LEN: Final = 60

_WHITESPACE_RUN: Final = re.compile(r"\s+")


def normalize_display_name(raw: str | None) -> str | None:
    """Canonical storage form of a user-supplied display name.

    Returns ``None`` for anything blank. Raises ``ValueError`` for a
    name carrying control characters or exceeding
    :data:`MAX_DISPLAY_NAME_LEN`; the route turns that into a 422.
    """
    if raw is None:
        return None
    # NFC first: a decomposed name must be measured and compared in the
    # form it will be stored in, not the form it arrived in.
    value = unicodedata.normalize("NFC", raw)
    # Reject Cc (control) and Cf (format) characters, but allow tab which is
    # the only control character a person plausibly types. Newlines, carriage
    # returns, and C1/format codepoints are rejected here before the whitespace
    # collapse so they raise an error rather than being silently converted.
    if any(unicodedata.category(ch) in ("Cc", "Cf") and ch != "\t" for ch in value):
        raise ValueError("display name may not contain control characters")
    value = _WHITESPACE_RUN.sub(" ", value).strip()
    if not value:
        return None
    if len(value) > MAX_DISPLAY_NAME_LEN:
        raise ValueError(f"display name may be at most {MAX_DISPLAY_NAME_LEN} characters")
    return value
