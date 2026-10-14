"""``splitsmith.derived``: a shared derived file is published whole or not at all.

The 2026-10-13 incident: a phone opening a stage fired three requests at once;
one pulled the 720p rendition from storage straight into its final path while
another ran ffmpeg on the half-written file and cached a short WAV that was
served to the phone (stage 8: 10.475 s of 28.223 s).
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import pytest

from splitsmith import derived


def _write_wav(path: Path, seconds: float, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


def test_a_reader_never_sees_the_file_before_it_is_complete(tmp_path: Path) -> None:
    final = tmp_path / "out.bin"
    seen_while_writing: list[bool] = []

    def make(partial: Path) -> None:
        with partial.open("wb") as f:
            f.write(b"half")
            f.flush()
            seen_while_writing.append(final.exists())
            f.write(b" and the rest")

    assert derived.produce(final, fresh=final.exists, make=make) is True
    assert seen_while_writing == [False]
    assert final.read_bytes() == b"half and the rest"
    assert list(tmp_path.glob("*.partial*")) == []


def test_locked_producers_make_it_once_and_the_rest_reuse_it(tmp_path: Path) -> None:
    final = tmp_path / "out.bin"
    calls: list[int] = []
    results: list[bool] = []

    def make(partial: Path) -> None:
        calls.append(1)
        time.sleep(0.2)
        partial.write_bytes(b"done")

    def run() -> None:
        results.append(derived.produce(final, fresh=final.exists, make=make, lock=True))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert sorted(results) == [False, False, False, True]
    assert final.read_bytes() == b"done"


def test_a_result_that_fails_its_check_is_never_published(tmp_path: Path) -> None:
    final = tmp_path / "out.bin"
    final.write_bytes(b"previous good")

    with pytest.raises(derived.DerivedFileError, match="too short"):
        derived.produce(
            final,
            fresh=lambda: False,
            make=lambda p: p.write_bytes(b"bad"),
            check=lambda p: "too short",
        )
    assert final.read_bytes() == b"previous good"
    assert list(tmp_path.glob("*.partial*")) == []


def test_unlocked_producers_racing_still_publish_a_whole_file(tmp_path: Path) -> None:
    final = tmp_path / "out.bin"

    def make(partial: Path) -> None:
        with partial.open("wb") as f:
            for _ in range(50):
                f.write(b"x" * 1000)
                time.sleep(0.001)

    threads = [
        threading.Thread(
            target=derived.produce, kwargs={"final": final, "fresh": lambda: False, "make": make}
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert final.read_bytes() == b"x" * 50_000
    assert list(tmp_path.glob("*.partial*")) == []


def test_a_failed_make_leaves_no_partial(tmp_path: Path) -> None:
    final = tmp_path / "out.bin"

    def make(partial: Path) -> None:
        partial.write_bytes(b"torn")
        raise RuntimeError("ffmpeg died")

    with pytest.raises(RuntimeError, match="ffmpeg died"):
        derived.produce(final, fresh=final.exists, make=make)
    assert not final.exists()
    assert list(tmp_path.glob("*.partial*")) == []


def test_the_partial_keeps_the_suffix_so_ffmpeg_picks_the_format(tmp_path: Path) -> None:
    p = derived.unique_partial(tmp_path / "stage9_cam_x_audit.wav")
    assert p.suffix == ".wav"
    assert ".partial" in p.name
    assert p != derived.unique_partial(tmp_path / "stage9_cam_x_audit.wav")


def test_wav_check_refuses_empty_and_unreadable_and_short(tmp_path: Path) -> None:
    good = tmp_path / "good.wav"
    _write_wav(good, 2.0)
    assert derived.wav_problem(good) is None
    assert derived.wav_problem(good, expect_seconds=2.0) is None

    empty = tmp_path / "empty.wav"
    _write_wav(empty, 0.0)
    assert "no audio" in derived.wav_problem(empty)

    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav")
    assert "unreadable" in derived.wav_problem(junk)

    assert "short" in derived.wav_problem(good, expect_seconds=3.0)


class _Storage:
    def __init__(self, blobs: dict[str, bytes], *, fail_after: int | None = None) -> None:
        self.blobs = blobs
        self.fail_after = fail_after

    def exists(self, key: str) -> bool:
        return key in self.blobs

    def open_stream(self, key: str):
        import io

        data = self.blobs[key]
        if self.fail_after is None:
            return io.BytesIO(data)

        fail_after = self.fail_after

        class Torn(io.BytesIO):
            def read(self, n: int = -1) -> bytes:
                if self.tell() >= fail_after:
                    raise OSError("connection reset")
                return super().read(min(n if n > 0 else fail_after, fail_after - self.tell()))

        return Torn(data)


def test_a_download_lands_whole(tmp_path: Path) -> None:
    final = tmp_path / "web.mp4"
    assert derived.download(_Storage({"k": b"x" * 100_000}), "k", final) is True
    assert final.read_bytes() == b"x" * 100_000


def test_a_torn_download_leaves_neither_final_nor_partial(tmp_path: Path) -> None:
    final = tmp_path / "web.mp4"
    assert derived.download(_Storage({"k": b"x" * 100_000}, fail_after=10_000), "k", final) is False
    assert not final.exists()
    assert list(tmp_path.glob("*.partial*")) == []


def test_a_missing_key_is_not_a_download(tmp_path: Path) -> None:
    assert derived.download(_Storage({}), "k", tmp_path / "web.mp4") is False
