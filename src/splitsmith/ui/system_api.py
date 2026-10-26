"""Local-only system routes: is Playwright's Chromium installed, and install it.

The desktop app has no terminal, so ``overlay_raster.INSTALL_HINT`` is
not actionable there. ``GET /api/system/chromium`` answers whether the
rasterizer can launch (the same probe a render performs) and
``POST /api/system/chromium/install`` runs ``playwright install`` for the
headless-shell channel as a job, so the SPA follows it on the progress
strip. Hosted containers preinstall the browser and these routes 404
there. This module must not import ``server``; it reaches state through
``request.app.state``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..overlay.raster import (
    CHROMIUM_CHANNEL,
    ChromiumRasterizer,
    Rasterizer,
    RasterizerUnavailableError,
    renderer_installed,
)
from .jobs import Job, JobHandle

router = APIRouter()

JOB_KIND = "chromium_install"

#: Test seam, same shape as ``export_preview_api.rasterizer_factory``.
rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]] = ChromiumRasterizer


class ChromiumStatus(BaseModel):
    installed: bool
    channel: str = CHROMIUM_CHANNEL


def _hosted() -> bool:
    return os.environ.get("SPLITSMITH_MODE") == "hosted"


def _local_only() -> None:
    if _hosted():
        raise HTTPException(status_code=404, detail="not found")


def _probe() -> bool:
    """Launch and close the rasterizer: the one check that means a render will work."""
    try:
        with rasterizer_factory():
            return True
    except RasterizerUnavailableError:
        return False


def _run_install(argv: list[str], handle: JobHandle) -> int:
    """Run ``argv`` streaming its output lines into the job message. Test seam."""
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
    )
    handle.attach_subprocess(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            handle.check_cancel()
            text = line.strip()
            if text:
                handle.update(message=text[:200])
        return proc.wait()
    finally:
        handle.detach_subprocess()


def run_chromium_install(handle: JobHandle) -> None:
    """Job body for ``chromium_install``: ``python -m playwright install <channel>``.

    ``sys.executable`` is the interpreter serving this request, so inside
    the desktop bundle it is the bundled one and the browser lands in
    Playwright's own cache under the user's home, never in the sealed app.
    """
    argv = [sys.executable, "-m", "playwright", "install", CHROMIUM_CHANNEL]
    handle.update(progress=0.0, message="Downloading Chromium headless shell")
    code = _run_install(argv, handle)
    if code != 0:
        raise RuntimeError(f"playwright install exited {code}")
    handle.update(progress=1.0, message="Chromium installed")


def _jobs(request: Request) -> Any:
    return request.app.state.splitsmith_state.jobs


#: Set to skip the startup install (tests: a fresh config dir must not start
#: a 260 MB download per test, as with ``SPLITSMITH_NO_MODEL_PREFETCH``).
ENV_NO_RENDERER_PREFETCH = "SPLITSMITH_NO_RENDERER_PREFETCH"

#: What an export that needs the renderer answers while it is missing.
RENDERER_MISSING = (
    "This export draws overlays or cards, which need the renderer, and it is not installed yet. "
    "Install renderer on the Export page (260 MB, once), then export again."
)


async def maybe_install_renderer(jobs: Any) -> None:
    """Start the renderer install at startup when it is missing (local only),
    as the detection models are fetched: a desktop user has no terminal, and
    the only button for it used to appear only when a card preview failed,
    so an export dropped every overlay and card instead (0.83.0). Reads the
    disk (``renderer_installed``), never launches a browser at boot; an
    unreadable answer starts nothing."""
    if _hosted() or os.environ.get(ENV_NO_RENDERER_PREFETCH, "").lower() in {"1", "true", "yes", "on"}:
        return
    if renderer_installed() is not False:
        return
    if await jobs.find_active(kind=JOB_KIND) is not None:
        return
    await jobs.submit(kind=JOB_KIND)


def require_renderer() -> None:
    """409 with :data:`RENDERER_MISSING` when this local install has no
    renderer: the export would otherwise run and leave out every overlay
    and card, which reads as a finished video with things missing."""
    if not _hosted() and renderer_installed() is False:
        raise HTTPException(status_code=409, detail=RENDERER_MISSING)


@router.get("/api/system/chromium", response_model=ChromiumStatus)
async def chromium_status() -> ChromiumStatus:
    _local_only()
    return ChromiumStatus(installed=await run_in_threadpool(_probe))


@router.post("/api/system/chromium/install", response_model=Job, status_code=202)
async def chromium_install(request: Request) -> Job:
    _local_only()
    jobs = _jobs(request)
    running = await jobs.find_active(kind=JOB_KIND)
    if running is not None:
        return running
    return await jobs.submit(kind=JOB_KIND)
