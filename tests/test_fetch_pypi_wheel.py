import hashlib
import importlib.util
import urllib.error
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "fetch_pypi_wheel", Path(__file__).parents[1] / "scripts" / "ci" / "fetch_pypi_wheel.py"
)
fpw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fpw)


def _release(*files: dict) -> dict:
    return {"urls": list(files)}


def _file(name: str, kind: str = "bdist_wheel", sha: str = "ab" * 32) -> dict:
    return {"filename": name, "packagetype": kind, "url": f"https://files/{name}", "digests": {"sha256": sha}}


def test_picks_the_pure_wheel_over_the_sdist() -> None:
    rel = _release(_file("splitsmith-1.2.3.tar.gz", "sdist"), _file("splitsmith-1.2.3-py3-none-any.whl"))
    assert fpw.pick_wheel(rel) == (
        "splitsmith-1.2.3-py3-none-any.whl",
        "https://files/splitsmith-1.2.3-py3-none-any.whl",
        "ab" * 32,
    )


def test_no_pure_wheel_is_an_error() -> None:
    with pytest.raises(fpw.NoWheel):
        fpw.pick_wheel(_release(_file("splitsmith-1.2.3.tar.gz", "sdist")))


def test_fetch_retries_until_the_version_appears_then_verifies(tmp_path: Path) -> None:
    body = b"wheel-bytes"
    sha = hashlib.sha256(body).hexdigest()
    rel = _release(_file("splitsmith-1.2.3-py3-none-any.whl", sha=sha))
    answers = [None, None, rel]  # 404, 404, then the release
    out = fpw.fetch(
        "1.2.3",
        tmp_path,
        get_json=lambda url: answers.pop(0),
        get_bytes=lambda url: body,
        attempts=5,
        sleep=lambda s: None,
    )
    assert out == tmp_path / "splitsmith-1.2.3-py3-none-any.whl"
    assert out.read_bytes() == body


def test_fetch_gives_up_after_its_attempts(tmp_path: Path) -> None:
    with pytest.raises(fpw.NotYetPublished):
        fpw.fetch(
            "1.2.3",
            tmp_path,
            get_json=lambda url: None,
            get_bytes=lambda url: b"",
            attempts=3,
            sleep=lambda s: None,
        )


def test_fetch_refuses_a_sha_mismatch(tmp_path: Path) -> None:
    rel = _release(_file("splitsmith-1.2.3-py3-none-any.whl", sha="00" * 32))
    with pytest.raises(fpw.ShaMismatch):
        fpw.fetch(
            "1.2.3",
            tmp_path,
            get_json=lambda url: rel,
            get_bytes=lambda url: b"x",
            attempts=1,
            sleep=lambda s: None,
        )
    assert not (tmp_path / "splitsmith-1.2.3-py3-none-any.whl").exists()


def test_fetch_retries_a_503_then_succeeds(tmp_path: Path) -> None:
    body = b"wheel-bytes"
    sha = hashlib.sha256(body).hexdigest()
    rel = _release(_file("splitsmith-1.2.3-py3-none-any.whl", sha=sha))
    calls: list[str] = []

    def get_json(url: str) -> dict:
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)
        return rel

    out = fpw.fetch(
        "1.2.3", tmp_path, get_json=get_json, get_bytes=lambda url: body, attempts=5, sleep=lambda s: None
    )
    assert out == tmp_path / "splitsmith-1.2.3-py3-none-any.whl"
    assert out.read_bytes() == body
    assert len(calls) == 2


def test_fetch_retries_a_urlerror_then_succeeds(tmp_path: Path) -> None:
    body = b"wheel-bytes"
    sha = hashlib.sha256(body).hexdigest()
    rel = _release(_file("splitsmith-1.2.3-py3-none-any.whl", sha=sha))
    calls: list[str] = []

    def get_json(url: str) -> dict:
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError("connection refused")
        return rel

    out = fpw.fetch(
        "1.2.3", tmp_path, get_json=get_json, get_bytes=lambda url: body, attempts=5, sleep=lambda s: None
    )
    assert out == tmp_path / "splitsmith-1.2.3-py3-none-any.whl"
    assert out.read_bytes() == body
    assert len(calls) == 2


def test_fetch_retries_a_timeout_then_succeeds(tmp_path: Path) -> None:
    body = b"wheel-bytes"
    sha = hashlib.sha256(body).hexdigest()
    rel = _release(_file("splitsmith-1.2.3-py3-none-any.whl", sha=sha))
    calls: list[str] = []

    def get_json(url: str) -> dict:
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return rel

    out = fpw.fetch(
        "1.2.3", tmp_path, get_json=get_json, get_bytes=lambda url: body, attempts=5, sleep=lambda s: None
    )
    assert out == tmp_path / "splitsmith-1.2.3-py3-none-any.whl"
    assert out.read_bytes() == body
    assert len(calls) == 2


def test_fetch_raises_a_403_immediately_without_retrying(tmp_path: Path) -> None:
    calls: list[str] = []

    def get_json(url: str) -> dict:
        calls.append(url)
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    with pytest.raises(urllib.error.HTTPError):
        fpw.fetch(
            "1.2.3", tmp_path, get_json=get_json, get_bytes=lambda url: b"", attempts=5, sleep=lambda s: None
        )
    assert len(calls) == 1
