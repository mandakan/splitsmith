"""Local HTTP server for the audit-only review SPA.

Stdlib only -- no FastAPI / starlette dependency. The server is single-purpose
and short-lived: it serves one fixture and shuts down when the user kills the
process. Designed to run on localhost only; no auth.

API:
    GET  /api/fixture       -> fixture JSON
    PUT  /api/fixture       -> overwrite fixture JSON (.bak created first)
    GET  /api/audio         -> the wav sibling of the fixture (Range supported)
    GET  /api/video         -> --video file if provided (Range supported)
    GET  /api/meta          -> { has_video, video_offset_seconds, fixture_path }
    GET  /                  -> index.html
    GET  /<asset>           -> static asset from review_static/

Range support is required for browsers to seek inside <video> elements; we
also implement it for /api/audio for symmetry, though wavesurfer downloads the
full clip up front for short fixtures.
"""

from __future__ import annotations

import json
import mimetypes
import socketserver
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

STATIC_ROOT = Path(__file__).parent / "review_static"


@dataclass(frozen=True)
class ReviewConfig:
    """All the per-invocation paths the request handler needs."""

    fixture_path: Path
    audio_path: Path
    video_path: Path | None
    video_offset_seconds: float


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """ThreadingTCPServer with allow_reuse_address so SIGINT + restart works."""

    allow_reuse_address = True
    daemon_threads = True


def make_server(host: str, port: int, config: ReviewConfig) -> _ThreadedTCPServer:
    """Construct the threaded TCP server. Caller runs ``server.serve_forever()``."""

    handler_factory = _make_handler(config)
    return _ThreadedTCPServer((host, port), handler_factory)


def _make_handler(config: ReviewConfig) -> type[BaseHTTPRequestHandler]:
    """Bind the per-invocation config into a request handler class."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Suppress noisy default access log; the Typer command prints its own.
            return

        # -- Routing ---------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler convention)
            path = self.path.split("?", 1)[0]
            if path == "/api/fixture":
                self._serve_fixture()
            elif path == "/api/audio":
                self._serve_file(config.audio_path)
            elif path == "/api/video":
                if config.video_path is None:
                    self._send_simple(HTTPStatus.NOT_FOUND, "no video configured")
                else:
                    self._serve_file(config.video_path)
            elif path == "/api/meta":
                self._serve_meta()
            elif path == "/" or path == "":
                self._serve_static("index.html")
            else:
                self._serve_static(path.lstrip("/"))

        def do_PUT(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] == "/api/fixture":
                self._handle_put_fixture()
            else:
                self._send_simple(HTTPStatus.NOT_FOUND, "unknown route")

        # -- Handlers --------------------------------------------------------

        def _serve_meta(self) -> None:
            payload = {
                "has_video": config.video_path is not None,
                "video_offset_seconds": config.video_offset_seconds,
                "fixture_path": str(config.fixture_path),
                "video_filename": (
                    config.video_path.name if config.video_path is not None else None
                ),
            }
            body = json.dumps(payload).encode("utf-8")
            self._send_bytes(HTTPStatus.OK, body, "application/json")

        def _serve_fixture(self) -> None:
            try:
                body = config.fixture_path.read_bytes()
            except OSError as exc:
                self._send_simple(HTTPStatus.INTERNAL_SERVER_ERROR, f"read failed: {exc}")
                return
            self._send_bytes(HTTPStatus.OK, body, "application/json")

        def _handle_put_fixture(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_simple(HTTPStatus.BAD_REQUEST, f"invalid json: {exc}")
                return
            if not isinstance(parsed, dict):
                self._send_simple(HTTPStatus.BAD_REQUEST, "fixture root must be a JSON object")
                return
            try:
                _atomic_write_with_backup(config.fixture_path, parsed)
            except OSError as exc:
                self._send_simple(HTTPStatus.INTERNAL_SERVER_ERROR, f"write failed: {exc}")
                return
            self._send_bytes(HTTPStatus.OK, b'{"ok":true}', "application/json")

        def _serve_static(self, rel_path: str) -> None:
            # Reject path traversal: resolve and ensure the result is inside STATIC_ROOT.
            target = (STATIC_ROOT / rel_path).resolve()
            try:
                target.relative_to(STATIC_ROOT.resolve())
            except ValueError:
                self._send_simple(HTTPStatus.FORBIDDEN, "path escape")
                return
            if not target.is_file():
                self._send_simple(HTTPStatus.NOT_FOUND, f"no such file: {rel_path}")
                return
            ctype, _ = mimetypes.guess_type(target.name)
            self._serve_file(target, content_type=ctype or "application/octet-stream")

        # -- File helpers ----------------------------------------------------

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.is_file():
                self._send_simple(HTTPStatus.NOT_FOUND, f"missing: {path.name}")
                return
            ctype = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            size = path.stat().st_size
            range_header = self.headers.get("Range")
            if range_header is None:
                self._send_full_file(path, size, ctype)
            else:
                self._send_ranged_file(path, size, ctype, range_header)

        def _send_full_file(self, path: Path, size: int, ctype: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with path.open("rb") as f:
                _copy_stream(f, self.wfile)

        def _send_ranged_file(self, path: Path, size: int, ctype: str, range_header: str) -> None:
            start, end = _parse_range(range_header, size)
            if start is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                _copy_stream(f, self.wfile, max_bytes=length)

        # -- Generic helpers -------------------------------------------------

        def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_simple(self, status: HTTPStatus, message: str) -> None:
            self._send_bytes(status, message.encode("utf-8"), "text/plain; charset=utf-8")

    return Handler


def _atomic_write_with_backup(target: Path, parsed: dict) -> None:
    """Write JSON to ``target`` atomically; keep the previous version as ``.bak``."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    serialized = json.dumps(parsed, indent=2) + "\n"
    tmp.write_text(serialized, encoding="utf-8")
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        # Replace any existing .bak so we always have the previous version.
        if backup.exists():
            backup.unlink()
        target.replace(backup)
    tmp.replace(target)


def _copy_stream(src, dst, max_bytes: int | None = None, chunk: int = 65536) -> None:
    remaining = max_bytes
    while True:
        if remaining is not None and remaining <= 0:
            return
        read_size = chunk if remaining is None else min(chunk, remaining)
        data = src.read(read_size)
        if not data:
            return
        dst.write(data)
        if remaining is not None:
            remaining -= len(data)


def _parse_range(header: str, size: int) -> tuple[int | None, int]:
    """Parse a single ``bytes=start-end`` range. Returns (None, 0) if unsatisfiable."""
    if not header.startswith("bytes="):
        return (None, 0)
    spec = header[len("bytes=") :].split(",", 1)[0].strip()
    if "-" not in spec:
        return (None, 0)
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            # Suffix range: bytes=-N -> the last N bytes.
            length = int(end_s)
            if length <= 0:
                return (None, 0)
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return (None, 0)
    if start >= size or end >= size or start > end:
        return (None, 0)
    return (start, end)
