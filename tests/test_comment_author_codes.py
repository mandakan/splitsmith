"""Author-code derivation (#867).

The code is the disambiguator: two commenters posting under the same or
a similar name are told apart by it. That makes three properties
load-bearing -- it is stable for a given key, it is not the raw user id,
and the account and pseudonym branches feed the HMAC different keys but
produce codes from the same alphabet so neither is identifiable by shape.
"""

from __future__ import annotations

from splitsmith.comment_identity import (
    AUTHOR_CODE_ALPHABET,
    AUTHOR_CODE_LEN,
    author_code_for,
    derive_author_code,
)

SECRET = b"test-secret"


def test_code_is_the_declared_length_and_alphabet() -> None:
    code = derive_author_code("01JABCDEFGHJKMNPQRSTVWXYZ0", secret=SECRET)
    assert len(code) == AUTHOR_CODE_LEN
    assert set(code) <= set(AUTHOR_CODE_ALPHABET)


def test_the_alphabet_omits_lookalike_characters() -> None:
    """Crockford base32. I, L, O and U are absent so a code read aloud or
    copied by eye does not collide with a neighbour."""
    for ch in "ILOU":
        assert ch not in AUTHOR_CODE_ALPHABET


def test_code_is_stable_for_a_key() -> None:
    assert derive_author_code("key-a", secret=SECRET) == derive_author_code("key-a", secret=SECRET)


def test_different_keys_give_different_codes() -> None:
    assert derive_author_code("key-a", secret=SECRET) != derive_author_code("key-b", secret=SECRET)


def test_code_is_not_the_raw_key() -> None:
    """A ULID encodes its creation time, so publishing one leaks account
    age. The code must not contain it."""
    user_id = "01JABCDEFGHJKMNPQRSTVWXYZ0"
    assert derive_author_code(user_id, secret=SECRET) not in user_id


def test_the_secret_changes_the_code() -> None:
    assert derive_author_code("key-a", secret=b"one") != derive_author_code("key-a", secret=b"two")


def test_account_authors_key_off_the_user_id() -> None:
    code = author_code_for(
        author_kind="account",
        author_user_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert code == derive_author_code("01JABCDEFGHJKMNPQRSTVWXYZ0", secret=SECRET)


def test_handle_authors_key_off_the_author_key_hash() -> None:
    code = author_code_for(
        author_kind="handle",
        author_user_id=None,
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert code == derive_author_code("deadbeef", secret=SECRET)


def test_an_account_row_with_no_user_id_falls_back_to_the_key_hash() -> None:
    """author_user_id is ON DELETE SET NULL, so an account author whose
    account was deleted keeps author_kind='account' with a NULL id. It
    must still get a code rather than raising."""
    code = author_code_for(
        author_kind="account",
        author_user_id=None,
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert code == derive_author_code("deadbeef", secret=SECRET)


def test_one_browser_posting_signed_in_and_signed_out_gets_two_codes() -> None:
    """The code identifies the author, not the browser. Posting under an
    account and posting anonymously from the same browser are two
    different authors and must read as two."""
    signed_in = author_code_for(
        author_kind="account",
        author_user_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    anonymous = author_code_for(
        author_kind="handle",
        author_user_id=None,
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert signed_in != anonymous
