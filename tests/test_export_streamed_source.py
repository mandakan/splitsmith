"""A hosted stage export cuts its lossless trim from a presigned URL (#1674).

The export reads one window of each camera's source, like the audit trim
(``tests/test_trim_streamed_source.py``). Run A5 downloaded 48 sources for
48 exports into Cloud Run's in-memory disk; read over HTTP the cut fetches
about its window and writes nothing but the export itself. A read that
reports errors is cut again from a download.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_hosted_web_only_trims import SOURCE_KEY, _setup
from tests.test_trim_streamed_source import SIGNED, _inputs, _presigning
from tests.ui._helpers import _wait_for_job

TRIM_ONLY = {"write_trim": True, "write_csv": False, "write_fcpxml": False, "write_report": False}


@pytest.fixture
def match_context():  # type: ignore[no-untyped-def]
    tokens: list = []
    yield tokens
    for var, token in reversed(tokens):
        var.reset(token)


def _export(client) -> dict:  # type: ignore[no-untyped-def]
    job = client.post("/api/shooters/me/stages/1/export", json=TRIM_ONLY).json()
    return _wait_for_job(client, job["id"])


def test_a_hosted_export_cuts_from_a_url_and_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, _project, root, video, _cuts = _setup(tmp_path, monkeypatch, match_context)
    _presigning(storage, monkeypatch)
    seen = _inputs(monkeypatch)

    done = _export(client)

    assert done["status"] == "succeeded", done
    assert seen == [SIGNED]
    assert SOURCE_KEY not in storage.fetched
    assert not (root / video.path).exists()


def test_a_failed_streamed_export_read_cuts_again_from_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, _project, root, video, _cuts = _setup(tmp_path, monkeypatch, match_context)
    _presigning(storage, monkeypatch)
    seen = _inputs(monkeypatch, fail_url=True)

    done = _export(client)

    assert done["status"] == "succeeded", done
    assert seen == [SIGNED, (root / video.path).resolve()]
    assert SOURCE_KEY in storage.fetched


def test_an_export_without_presigned_urls_downloads_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, _project, root, video, _cuts = _setup(tmp_path, monkeypatch, match_context)
    seen = _inputs(monkeypatch)

    done = _export(client)

    assert done["status"] == "succeeded", done
    assert seen == [(root / video.path).resolve()]
    assert SOURCE_KEY in storage.fetched
