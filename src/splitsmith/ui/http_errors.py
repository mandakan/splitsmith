"""Shared structured-HTTPException helpers for the UI routers.

Exists because ``server.py`` and the domain routers lifted out of it
under #919 both need the same error shapes, and a router must never
import ``server`` (that would be a load-time cycle -- ``server`` imports
the routers' request models back). Anything two of them share lands
here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


def ensure_source_reachable(stage_number: int | None, source: Path) -> None:
    """Raise a structured 424 when ``source`` doesn't exist on disk.

    The SPA reads ``detail.code == "source_unreachable"`` to render a
    uniform "reconnect the USB / SD card" message wherever a source-bound
    operation is invoked (detect-beep, audit-mode trim, beep preview,
    video stream, export). Callers handle the "no primary" check with
    their endpoint-specific status code before calling this -- the helper
    only handles the upstream-dependency-offline case.
    """
    if source.exists():
        return
    raise HTTPException(
        status_code=424,
        detail={
            "code": "source_unreachable",
            "stage_number": stage_number,
            "path": str(source),
            "message": (
                "Source video"
                + (f" for stage {stage_number}" if stage_number is not None else "")
                + f" is not reachable: {source}. If it lives on external "
                f"storage (USB drive, SD card), reconnect and try again."
            ),
        },
    )
