"""Tests for the hosted-mode ``S3Storage`` backend.

Uses the ``moto`` in-memory S3 mock so the round-trip exercises
the real boto3 client without standing up MinIO or a live bucket.
The same Protocol assertions as ``test_storage.py``, plus the
metadata fields (``etag``, ``last_modified``) that the
filesystem backend doesn't populate.
"""

from __future__ import annotations

import pytest

moto = pytest.importorskip("moto")
import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

from splitsmith.storage import S3Storage, StorageObject  # noqa: E402

BUCKET = "splitsmith-test"


@pytest.fixture
def s3_client():
    """Spin up an in-memory S3 with one bucket. Yields a boto3
    client so tests can both seed via the backend under test and
    inspect via the raw client when needed.
    """
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _storage(s3_client, *, prefix: str = "") -> S3Storage:
    return S3Storage(bucket=BUCKET, prefix=prefix, client=s3_client)


def test_round_trip_write_then_read(s3_client) -> None:
    storage = _storage(s3_client)
    storage.write_bytes("hello.txt", b"world")

    assert storage.read_bytes("hello.txt") == b"world"


def test_upload_stream_round_trip(s3_client) -> None:
    """boto3 upload_fileobj path must land the same bytes as put_object
    and report the streamed size back to the caller."""
    import io

    storage = _storage(s3_client)
    # Larger than the default 8 MiB multipart threshold would force a
    # multipart upload against real S3; moto handles either path.
    payload = b"X" * 17 + b"end"
    written = storage.upload_stream("raw/clip.bin", io.BytesIO(payload))

    assert written == len(payload)
    assert storage.read_bytes("raw/clip.bin") == payload


def test_upload_stream_rejects_traversal(s3_client) -> None:
    import io

    storage = _storage(s3_client)
    with pytest.raises(ValueError, match="must be relative"):
        storage.upload_stream("../escape.bin", io.BytesIO(b"x"))


def test_write_overwrites_existing(s3_client) -> None:
    storage = _storage(s3_client)
    storage.write_bytes("k", b"v1")
    storage.write_bytes("k", b"v2")

    assert storage.read_bytes("k") == b"v2"


def test_read_missing_key_raises_file_not_found(s3_client) -> None:
    storage = _storage(s3_client)

    with pytest.raises(FileNotFoundError):
        storage.read_bytes("nope")


def test_exists_returns_true_after_write(s3_client) -> None:
    storage = _storage(s3_client)
    assert not storage.exists("k")

    storage.write_bytes("k", b"v")
    assert storage.exists("k")


def test_stat_returns_none_for_missing(s3_client) -> None:
    storage = _storage(s3_client)

    assert storage.stat("nope") is None


def test_stat_returns_size_etag_last_modified(s3_client) -> None:
    storage = _storage(s3_client)
    storage.write_bytes("k", b"hello")

    info = storage.stat("k")
    assert isinstance(info, StorageObject)
    assert info.path == "k"
    assert info.size == 5
    # S3 returns an ETag (md5 for non-multipart) and a Last-Modified.
    # FilesystemStorage doesn't, so this is a per-backend richer field.
    assert info.etag is not None
    assert info.last_modified is not None


def test_list_walks_objects_under_prefix(s3_client) -> None:
    storage = _storage(s3_client)
    storage.write_bytes("a.txt", b"x")
    storage.write_bytes("nested/b.txt", b"x")
    storage.write_bytes("nested/deep/c.txt", b"x")

    paths = sorted(obj.path for obj in storage.list(""))
    assert paths == ["a.txt", "nested/b.txt", "nested/deep/c.txt"]


def test_list_with_prefix_narrows_to_subtree(s3_client) -> None:
    storage = _storage(s3_client)
    storage.write_bytes("a/1.txt", b"x")
    storage.write_bytes("a/2.txt", b"x")
    storage.write_bytes("b/3.txt", b"x")

    paths = sorted(obj.path for obj in storage.list("a"))
    assert paths == ["a/1.txt", "a/2.txt"]


def test_list_missing_prefix_yields_nothing(s3_client) -> None:
    storage = _storage(s3_client)
    storage.write_bytes("a.txt", b"x")

    assert list(storage.list("nope")) == []


def test_delete_removes_object(s3_client) -> None:
    storage = _storage(s3_client)
    storage.write_bytes("k", b"v")

    storage.delete("k")

    assert not storage.exists("k")


def test_delete_missing_is_noop(s3_client) -> None:
    storage = _storage(s3_client)
    # S3 delete_object is a 204 for missing keys; the wrapper
    # preserves the same no-op contract FilesystemStorage gives.
    storage.delete("nope")


# Path-traversal guard: same set as test_storage.py so the two
# backends present identical safety contracts.
@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "../escape.txt",
        "nested/../../escape.txt",
        "a/../../b",
    ],
)
def test_traversal_or_absolute_paths_rejected(s3_client, bad_path: str) -> None:
    storage = _storage(s3_client)

    with pytest.raises(ValueError, match="must be relative"):
        storage.write_bytes(bad_path, b"x")


def test_prefix_scopes_writes_and_reads(s3_client) -> None:
    """Multi-tenant key isolation: one ``S3Storage`` per tenant
    prefix means the keys callers pass never collide with another
    tenant's, even though they use the same simple paths like
    ``audit/stage1.json``.
    """
    alice = _storage(s3_client, prefix="users/alice/projects/m1/")
    bob = _storage(s3_client, prefix="users/bob/projects/m1/")

    alice.write_bytes("audit/stage1.json", b"alice")
    bob.write_bytes("audit/stage1.json", b"bob")

    assert alice.read_bytes("audit/stage1.json") == b"alice"
    assert bob.read_bytes("audit/stage1.json") == b"bob"

    # The raw S3 view confirms the actual keys carry the prefix
    # (and that the two tenants live in disjoint key spaces).
    listed = sorted(obj["Key"] for obj in s3_client.list_objects_v2(Bucket=BUCKET).get("Contents", []))
    assert listed == [
        "users/alice/projects/m1/audit/stage1.json",
        "users/bob/projects/m1/audit/stage1.json",
    ]


def test_prefix_list_returns_relative_paths(s3_client) -> None:
    """The list() result strips the storage-root prefix so callers
    see the same shape they wrote (matches FilesystemStorage).
    """
    storage = _storage(s3_client, prefix="users/alice/projects/m1/")
    storage.write_bytes("audit/stage1.json", b"x")
    storage.write_bytes("audit/stage2.json", b"x")

    paths = sorted(obj.path for obj in storage.list("audit"))
    assert paths == ["audit/stage1.json", "audit/stage2.json"]


def test_satisfies_storage_protocol() -> None:
    """Structural-typing assertion: ``S3Storage`` is a
    drop-in :class:`Storage` so handlers typed against the
    Protocol can accept either backend.
    """
    from splitsmith.storage import Storage

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        store: Storage = S3Storage(bucket=BUCKET, client=client)
        assert store is not None
