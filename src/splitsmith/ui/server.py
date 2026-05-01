"""FastAPI app for the production UI.

Endpoints (locked v1 surface):

  GET  /api/health                  -- project metadata + schema version
  GET  /api/project                 -- full MatchProject dump
  POST /api/scoreboard/import       -- import an SSI Scoreboard JSON
  POST /api/videos/scan             -- enumerate videos in a folder, register them
  POST /api/videos/auto-match       -- run video_match.py heuristic, return suggestions
  POST /api/assignments/move        -- set role / unassign / move between stages

Design notes:
- Localhost only. No auth, no CORS configuration beyond what Vite needs in dev.
- The server holds a single ``MatchProject`` open at a time, identified by
  ``project_root`` at startup. Multi-project orchestration lives in the SPA.
- All on-disk mutations go through the project model's atomic save.
- The server re-loads the project from disk for every request (no caching), so
  external edits are visible without restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .project import (
    VIDEO_EXTENSIONS,
    MatchProject,
    ScoreboardImportConflictError,
    VideoRole,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "ui_static" / "dist"


@dataclass
class AppState:
    """Per-process state. One project root per server instance."""

    project_root: Path

    def load(self) -> MatchProject:
        return MatchProject.load(self.project_root)


class HealthResponse(BaseModel):
    status: str = "ok"
    project_name: str
    project_root: str
    schema_version: int


# Request bodies ----------------------------------------------------------


class ScoreboardImportRequest(BaseModel):
    data: dict[str, Any]
    overwrite: bool = False


class ScanRequest(BaseModel):
    source_dir: str
    auto_assign_primary: bool = True
    link_mode: str = "symlink"


class ScanResponse(BaseModel):
    registered: list[str]
    auto_assigned: dict[int, str]
    skipped: list[str]


class MoveRequest(BaseModel):
    video_path: str
    to_stage_number: int | None = None
    role: VideoRole = "secondary"


def create_app(*, project_root: Path, project_name: str) -> FastAPI:
    """Create the FastAPI app bound to a single match project on disk.

    The project is initialized on first call (idempotent), then the app keeps
    the root path and re-loads on every request that needs it. We avoid
    caching the model in memory so external edits to ``project.json`` are
    visible without restarting the server.
    """
    MatchProject.init(project_root, name=project_name)
    state = AppState(project_root=project_root.resolve())

    app = FastAPI(
        title="splitsmith UI",
        description="Production UI backend (issue #11/#12).",
        version="0.1.0",
    )

    # ----------------------------------------------------------------------
    # API
    # ----------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        project = state.load()
        return HealthResponse(
            project_name=project.name,
            project_root=str(state.project_root),
            schema_version=project.schema_version,
        )

    @app.get("/api/project")
    def get_project() -> JSONResponse:
        return JSONResponse(state.load().model_dump(mode="json"))

    @app.post("/api/scoreboard/import")
    def import_scoreboard(req: ScoreboardImportRequest) -> JSONResponse:
        project = state.load()
        try:
            project.import_scoreboard(req.data, overwrite=req.overwrite)
        except ScoreboardImportConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project.save(state.project_root)
        return JSONResponse(project.model_dump(mode="json"))

    @app.post("/api/videos/scan", response_model=ScanResponse)
    def scan_videos(req: ScanRequest) -> ScanResponse:
        source = Path(req.source_dir).expanduser()
        if not source.exists():
            raise HTTPException(status_code=400, detail=f"source dir not found: {source}")
        if not source.is_dir():
            raise HTTPException(status_code=400, detail=f"not a directory: {source}")
        if req.link_mode not in ("symlink", "copy"):
            raise HTTPException(status_code=400, detail="link_mode must be 'symlink' or 'copy'")

        project = state.load()
        registered: list[str] = []
        skipped: list[str] = []
        for entry in sorted(source.iterdir()):
            if entry.is_dir() or entry.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                video = project.register_video(
                    entry, state.project_root, link_mode=req.link_mode  # type: ignore[arg-type]
                )
            except (FileNotFoundError, ValueError) as exc:
                skipped.append(f"{entry.name}: {exc}")
                continue
            registered.append(str(video.path))

        auto_assigned: dict[int, str] = {}
        if req.auto_assign_primary:
            suggestions = project.auto_match(state.project_root)
            for stage_num, video_path in suggestions.items():
                stage = project.stage(stage_num)
                # Only auto-assign primary when the stage has no primary yet.
                if stage.primary() is not None:
                    continue
                project.assign_video(video_path, to_stage_number=stage_num, role="primary")
                auto_assigned[stage_num] = str(video_path)

        project.save(state.project_root)
        return ScanResponse(
            registered=registered,
            auto_assigned=auto_assigned,
            skipped=skipped,
        )

    @app.post("/api/videos/auto-match")
    def auto_match() -> JSONResponse:
        project = state.load()
        suggestions = project.auto_match(state.project_root)
        return JSONResponse({str(stage_num): str(path) for stage_num, path in suggestions.items()})

    @app.post("/api/assignments/move")
    def move_assignment(req: MoveRequest) -> JSONResponse:
        project = state.load()
        try:
            project.assign_video(
                Path(req.video_path),
                to_stage_number=req.to_stage_number,
                role=req.role,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        project.save(state.project_root)
        return JSONResponse(project.model_dump(mode="json"))

    # ----------------------------------------------------------------------
    # Static asset serving (SPA)
    # ----------------------------------------------------------------------
    #
    # In dev, the user runs ``npm run dev`` in ``ui_static/`` and Vite serves
    # the SPA on its own port, proxying ``/api/*`` to this backend. In that
    # mode ``STATIC_DIR`` may not exist; the API routes still work and the
    # browser hits the Vite dev server directly.
    #
    # In prod, ``ui_static/dist`` is built and we serve it here.

    if STATIC_DIR.exists():
        # Mount built assets at /assets (matches Vite's default output).
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        # SPA fallback: any non-API route returns index.html so the React
        # router can handle it client-side.
        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="api route not found")
            index = STATIC_DIR / "index.html"
            if not index.exists():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "SPA bundle not built. Run `npm run build` in "
                        "src/splitsmith/ui_static/ or use `npm run dev`."
                    ),
                )
            return FileResponse(index)

    return app


def serve(
    *,
    project_root: Path,
    project_name: str,
    host: str = "127.0.0.1",
    port: int = 5174,
    reload: bool = False,
) -> None:
    """Boot uvicorn synchronously. Used by the ``splitsmith ui`` CLI command."""
    import uvicorn

    if reload:
        # Reload mode requires an importable factory; pass the path string and
        # use environment variables to feed the project context. Simpler: just
        # log a warning and run without reload for now. Reload is a dev
        # convenience that we can wire properly when we have a real config.
        logger.warning("reload=True is not supported yet; running without reload")

    app = create_app(project_root=project_root, project_name=project_name)
    uvicorn.run(app, host=host, port=port, log_level="info")
