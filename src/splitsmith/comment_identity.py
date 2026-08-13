"""Server-derived display names for anonymous commenters.

The client mints one opaque ``author_key`` and keeps it in
localStorage. It never sends a display name, and the request model never
declares one -- if it did, anyone with ``curl`` could sign a comment with
the match owner's name, which is exactly the impersonation this design
set out to prevent.

The handle is ``HMAC(secret, author_key)`` indexed into a curated IPSC
wordlist, giving ``adjective noun NN`` -- "Prone Popper 47". The HMAC
secret is what stops an attacker grinding keys offline until one hashes
to a handle someone else is already using: without it the search space
is only ~102k and a laptop exhausts it instantly; with it, the only
attack left is posting repeatedly, which the rate limit sees.

Rotating the secret is safe. ``author_handle`` is denormalized onto every
comment row at write time, so existing comments keep the name they were
posted under; only a *new* comment from the same browser gets a new one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Final

SPLITSMITH_COMMENT_HANDLE_SECRET_ENV: Final = "SPLITSMITH_COMMENT_HANDLE_SECRET"

# Longest client-supplied author key accepted. The client mints 32 random
# bytes hex-encoded (64 chars); the cap is generous headroom that still
# bounds what reaches the HMAC.
MAX_AUTHOR_KEY_LEN: Final = 128

# Shortest client-supplied author key accepted (fix round 1, F7). The
# client always mints 32 random bytes hex-encoded (64 chars, well above
# this); without a floor a 1-character key posts fine even though
# identity, ``mine``, and self-delete all key off its hash - a short key
# is easy to guess or collide, which turns self-delete into anyone-delete
# for that handle. Chosen well under the real 64-char key so it never
# rejects a legitimate client, only implausibly short ones.
MIN_AUTHOR_KEY_LEN: Final = 32

ADJECTIVES: Final[tuple[str, ...]] = (
    "Steady",
    "Swift",
    "Silent",
    "Sharp",
    "Rapid",
    "Calm",
    "Bold",
    "Brisk",
    "Clean",
    "Crisp",
    "Eager",
    "Fast",
    "Flat",
    "Fluid",
    "Keen",
    "Level",
    "Lucky",
    "Nimble",
    "Precise",
    "Prone",
    "Quick",
    "Ready",
    "Rolling",
    "Smooth",
    "Snappy",
    "Solid",
    "Spare",
    "Tight",
    "Trusty",
    "Wide",
    "Willing",
    "Zeroed",
)

NOUNS: Final[tuple[str, ...]] = (
    "Alpha",
    "Charlie",
    "Delta",
    "Mike",
    "Popper",
    "Plate",
    "Star",
    "Squib",
    "Comstock",
    "Classifier",
    "Draw",
    "Hoser",
    "Berm",
    "Papa",
    "Port",
    "Reload",
    "Sierra",
    "Stage",
    "Steel",
    "Target",
    "Transition",
    "Trigger",
    "Wall",
    "Zebra",
    "Fault",
    "Gong",
    "Magwell",
    "Sight",
    "Holster",
    "Bay",
    "Squad",
    "Chrono",
)

# 32 * 32 * 100 = 102,400 distinct handles.
_NUMBERS: Final = 100

# Process-lifetime fallback when the env var is unset (local / dev). A
# random value would change handles on every restart, so this is a fixed
# string: local mode has one operator and no adversary to grind keys.
_DEV_SECRET: Final = b"splitsmith-local-comment-handles"


def handle_secret() -> bytes:
    """HMAC key for handle derivation.

    Hosted deploys set ``SPLITSMITH_COMMENT_HANDLE_SECRET``. An unset var
    falls back to a fixed dev value rather than a random one: a random
    per-process secret would hand every browser a new name on each
    redeploy, which reads as a bug rather than as security.
    """
    raw = os.environ.get(SPLITSMITH_COMMENT_HANDLE_SECRET_ENV, "").strip()
    return raw.encode("utf-8") if raw else _DEV_SECRET


def hash_author_key(author_key: str) -> str:
    """Storage form of the client's opaque key.

    Hashed so a database dump does not hand out the tokens that let
    someone delete other people's comments. Plain SHA-256 (not HMAC) is
    right here: the value it protects is high-entropy client randomness,
    not a guessable identifier, so there is nothing to brute-force.
    """
    return hashlib.sha256(author_key.encode("utf-8")).hexdigest()


def derive_handle(author_key: str, *, secret: bytes | None = None) -> str:
    """Deterministic IPSC-themed display name for an anonymous commenter.

    ``adjective noun NN``, e.g. "Prone Popper 47". Stable for a given
    key + secret, and unguessable in the other direction.
    """
    key = secret if secret is not None else handle_secret()
    digest = hmac.new(key, author_key.encode("utf-8"), hashlib.sha256).digest()
    value = int.from_bytes(digest[:8], "big")
    adjective = ADJECTIVES[value % len(ADJECTIVES)]
    noun = NOUNS[(value // len(ADJECTIVES)) % len(NOUNS)]
    number = (value // (len(ADJECTIVES) * len(NOUNS))) % _NUMBERS
    return f"{adjective} {noun} {number:02d}"


# Crockford base32: the digits plus the consonant-heavy letter set that
# omits I, L, O and U. Chosen over plain base32 so a code read aloud, or
# copied by eye off a comment thread, cannot be confused with a
# neighbouring one - which is the whole job of the code.
AUTHOR_CODE_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# 6 characters over a 32-symbol alphabet is 30 bits, ~1.07e9 codes.
AUTHOR_CODE_LEN: Final = 6

# Domain separation. The handle and the code are derived from the same
# secret and, for a pseudonymous author, from the same key -- without a
# prefix the two HMACs would be the same computation, and a change to
# one derivation could silently move the other.
_AUTHOR_CODE_DOMAIN: Final = b"author-code:"


def derive_author_code(key: str, *, secret: bytes | None = None) -> str:
    """Stable public identifier for a comment author.

    Six Crockford-base32 characters, deterministic for a given key +
    secret and not reversible to the key. Callers should prefer
    :func:`author_code_for`, which decides *which* key an author's code
    derives from; this function is the raw derivation.
    """
    material = secret if secret is not None else handle_secret()
    digest = hmac.new(material, _AUTHOR_CODE_DOMAIN + key.encode("utf-8"), hashlib.sha256).digest()
    value = int.from_bytes(digest[:8], "big")
    out = []
    for _ in range(AUTHOR_CODE_LEN):
        out.append(AUTHOR_CODE_ALPHABET[value % len(AUTHOR_CODE_ALPHABET)])
        value //= len(AUTHOR_CODE_ALPHABET)
    return "".join(out)


def author_code_for(
    *,
    author_kind: str,
    author_user_id: str | None,
    author_key_hash: str,
    secret: bytes | None = None,
) -> str:
    """The author code for one comment's author.

    An account author's code derives from their user id, so it is the
    same code across every browser they post from. A pseudonymous
    author's derives from the hashed browser key, which is the only
    identity they have. **Never the raw user id itself** -- it is the
    internal foreign key and a ULID encodes its creation time, so
    publishing it on an anonymous surface would leak account age.

    The single decision point for which key feeds the HMAC. The write
    path and the read-time fallback in ``ui/comments.to_out`` both call
    this, which is what makes a legacy row's computed code identical to
    the one the write path would have stored.

    ``author_user_id`` is ``ON DELETE SET NULL``, so an account author
    whose account was deleted arrives here as ``author_kind="account"``
    with no id. That falls back to the key hash rather than raising: the
    comment still needs a code, and the one thing it must not do is
    collide with another author's.
    """
    if author_kind == "account" and author_user_id:
        return derive_author_code(author_user_id, secret=secret)
    return derive_author_code(author_key_hash, secret=secret)
