"""The ``render_upload`` job (#1100, spec 2026-09-28 render-upload
addendum): the phone asked the desktop to render a match video and upload
it, as one command whose result is the video.

One job, not the match export's chained upload job: the command completes
from one job's outcome, and here a failed upload fails the request (the
upload is what was asked for), while a desk export keeps its render
"succeeded" when its chained upload fails.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .jobs import JobCancelled

#: The render's share of the bar; the upload gets the rest.
RENDER_SHARE = 0.8
UPLOAD_FAILED_PREFIX = "Rendered on the desktop, but the upload failed: "


class _Stage:
    """A job handle for one half of the job: progress maps onto
    ``[lo, hi]`` of the real bar, and ``set_result`` is kept here rather
    than becoming the job's (the render's payload is not the command's
    result)."""

    def __init__(self, handle: Any, lo: float, hi: float) -> None:
        self._handle, self._lo, self._hi = handle, lo, hi
        self.result: dict[str, Any] | None = None

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            progress = self._lo + (self._hi - self._lo) * progress
        self._handle.update(progress=progress, message=message)

    def set_result(self, payload: dict[str, Any]) -> None:
        self.result = payload

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def run_render_upload(
    handle: Any,
    *,
    render: Callable[[Any], None],
    upload: Callable[[Any, str], None],
) -> None:
    """Render through ``render(handle)``, then upload the MP4 it reports
    through ``upload(handle, filename)``. Each half gets a wrapped handle;
    the job's own result is the upload's ``{video_id, url,
    channel_title}``, which is what the command completes with."""
    rendering = _Stage(handle, 0.0, RENDER_SHARE)
    render(rendering)
    path = str((rendering.result or {}).get("fcpxml_path") or "")
    if not path.lower().endswith(".mp4"):
        raise RuntimeError("the render produced no MP4 to upload")
    handle.check_cancel()
    uploading = _Stage(handle, RENDER_SHARE, 1.0)
    try:
        upload(uploading, Path(path).name)
    except JobCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - reported to the phone as the command's reason
        raise RuntimeError(f"{UPLOAD_FAILED_PREFIX}{exc}") from exc
    record = uploading.result or {}
    handle.set_result({k: record.get(k) for k in ("video_id", "url", "channel_title")})
    handle.update(progress=1.0, message=f"Uploaded {record.get('url', '')}".strip())
