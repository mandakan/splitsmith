"""The sandboxed template loader (issue #1266): what a template page may
load, and the budgets that stop a stuck one. The browser tests run a
template that tries every way out it can and throws ``LEAK: ...`` from its
``seek()`` if any works; ``probe_template`` reports that as an error."""

from __future__ import annotations

import hashlib
import http.server
import io
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import look_sandbox
from splitsmith.look_template import TemplateContext, engine_block, shared_url
from splitsmith.overlay_raster import (
    ChromiumRasterizer,
    RasterizerUnavailableError,
    TemplateScriptError,
    TemplateTimeoutError,
)


def _png(path: Path, size: int = 4) -> Path:
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), (0, 200, 0, 255)).save(buf, format="PNG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return path


def _context(**data: object) -> TemplateContext:
    return TemplateContext(
        theme={"ink": "#ffffff"},
        data={"groups": [], **data},
        size={"width": 64, "height": 32},
        fps=30,
        engine=engine_block(css="body{}"),
        assets={"shared": shared_url()},
    )


# --- the allowlist, without a browser -------------------------------------------------


def test_the_context_names_files_on_the_virtual_origin_only(tmp_path: Path) -> None:
    template = tmp_path / "look" / "card.html"
    template.parent.mkdir()
    template.write_text("<p>hi</p>", encoding="utf-8")
    logo = _png(tmp_path / "shooter" / "identity" / "logo-abc.png")
    sandbox, ctx = look_sandbox.prepare(template, _context(logo=logo.as_uri()))
    script = ctx.init_script()
    assert "file://" not in script
    assert f"{look_sandbox.ORIGIN}/shared" in script
    logo_url = ctx.data["logo"]
    assert logo_url.startswith(f"{look_sandbox.ORIGIN}/file/")
    assert sandbox.resolve(logo_url) == logo.resolve()
    assert sandbox.resolve(f"{look_sandbox.ORIGIN}/look/card.html") == template.resolve()
    assert sandbox.resolve(f"{look_sandbox.ORIGIN}/shared/fit.js") is not None


def test_user_text_naming_a_file_is_never_mounted(tmp_path: Path) -> None:
    """A stage name, a club line or a title is user text in ``data``; only a
    ``logo`` value, the engine stylesheet and ``assets`` may name a file."""
    template = tmp_path / "look" / "card.html"
    template.parent.mkdir()
    template.write_text("<p>hi</p>", encoding="utf-8")
    secret = _png(tmp_path / "secret.png")
    sandbox, ctx = look_sandbox.prepare(
        template,
        _context(
            card={"text": secret.as_uri(), "info": [f"file://{secret}"]}, shooters=[{"club": secret.as_uri()}]
        ),
    )
    assert ctx.data["card"]["text"] == secret.as_uri()
    assert ctx.data["shooters"][0]["club"] == secret.as_uri()
    assert sandbox.files == {}


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/look/card.html",
        "http://look.invalid/look/card.html",
        "https://look.invalid/look/../outside.png",
        "https://look.invalid/look/%2e%2e/outside.png",
        "https://look.invalid/look/..%2Foutside.png",
        "https://look.invalid/etc/passwd",
        "https://look.invalid/proc/self/environ",
        "https://look.invalid/file/0000000000000000/logo.png",
        "https://look.invalid/look/",
        "https://look.invalid/shared/../../../../../../etc/hosts",
        "file:///etc/hosts",
    ],
)
def test_a_url_outside_the_mounts_resolves_to_nothing(tmp_path: Path, url: str) -> None:
    template = tmp_path / "look" / "card.html"
    template.parent.mkdir()
    template.write_text("<p>hi</p>", encoding="utf-8")
    _png(tmp_path / "outside.png")
    sandbox, _ctx = look_sandbox.prepare(template, _context())
    assert sandbox.resolve(url) is None


def test_a_symlink_out_of_the_look_is_refused(tmp_path: Path) -> None:
    template = tmp_path / "look" / "card.html"
    template.parent.mkdir()
    template.write_text("<p>hi</p>", encoding="utf-8")
    secret = _png(tmp_path / "secret.png")
    (template.parent / "badge.png").symlink_to(secret)
    sandbox, _ctx = look_sandbox.prepare(template, _context())
    assert sandbox.resolve(f"{look_sandbox.ORIGIN}/look/badge.png") is None


# --- a real page ------------------------------------------------------------------------


class _Counter(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 -- http.server's name
        self.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def listener() -> Iterator[int]:
    """A local HTTP server counting every request that reaches it."""
    _Counter.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Counter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()


@pytest.fixture
def raster() -> Iterator[ChromiumRasterizer]:
    try:
        with ChromiumRasterizer() as r:
            yield r
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")


_ESCAPES = """<!doctype html><html><body><script>
const s = window.splitsmith;
const loads = (src) => new Promise((ok) => {
  const img = new Image(); img.onload = () => ok(true); img.onerror = () => ok(false); img.src = src;
});
const fetches = (url) => fetch(url).then((r) => r.ok, () => false);
const socket = (url) => new Promise((ok) => {
  try { const ws = new WebSocket(url); ws.onopen = () => ok(true); ws.onerror = () => ok(false); }
  catch (e) { ok(false); }
  setTimeout(() => ok(false), 1500);
});
async function attempt() {
  const port = s.data.port;
  const tries = {
    outside_file_url: await loads(s.data.outside),
    outside_literal: await loads('file://' + s.data.outsidePath),
    etc_hosts: await fetches('file:///etc/hosts'),
    proc_environ: await fetches('file:///proc/self/environ'),
    dotdot: await loads('/look/../outside.png'),
    dotdot_encoded: await loads('/look/%2e%2e/outside.png'),
    another_logo: await loads(s.data.another),
    network_img: await loads('http://127.0.0.1:' + port + '/img.png'),
    network_fetch: await fetches('http://127.0.0.1:' + port + '/fetch'),
    outside_host: await fetches('https://example.com/'),
    socket_sent: await socket('ws://127.0.0.1:' + port + '/ws').then(() => false),
    huge: await loads('huge.png'),
  };
  const allowed = {
    own_logo: await loads(s.data.logo),
    look_badge: await loads('badge.png'),
    shared_script: await fetches(s.assets.shared + '/fit.js'),
  };
  const leaks = Object.entries(tries).filter(([, v]) => v).map(([k]) => k);
  const missing = Object.entries(allowed).filter(([, v]) => !v).map(([k]) => k);
  if (leaks.length || missing.length) {
    throw new Error('LEAK: ' + leaks.join(',') + ' MISSING: ' + missing.join(','));
  }
}
let done = null;
window.seek = () => (done = done || attempt());
</script></body></html>"""


def test_a_template_reaches_its_look_its_context_and_nothing_else(
    tmp_path: Path, raster: ChromiumRasterizer, listener: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(look_sandbox, "MAX_ASSET_BYTES", 20_000)
    look = tmp_path / "accounts" / "a" / "looks" / "club"
    template = look / "card.html"
    look.mkdir(parents=True)
    template.write_text(_ESCAPES, encoding="utf-8")
    _png(look / "badge.png")
    # Noise, which PNG cannot compress: well over the patched cap.
    Image.frombytes("RGBA", (200, 200), os.urandom(200 * 200 * 4)).save(look / "huge.png")
    assert (look / "huge.png").stat().st_size > 20_000
    outside = _png(tmp_path / "accounts" / "a" / "looks" / "outside.png")
    own_logo = _png(tmp_path / "accounts" / "a" / "identity" / "logo-1.png")
    other_logo = _png(tmp_path / "accounts" / "b" / "identity" / "logo-2.png")
    another = f"/file/{hashlib.sha256(str(other_logo.resolve()).encode()).hexdigest()[:16]}/logo-2.png"
    # ``outside`` is user text in ``data`` (a stage name can be anything): it
    # must not be mounted the way a ``logo`` is.
    ctx = _context(
        port=listener,
        logo=own_logo.as_uri(),
        outside=outside.as_uri(),
        outsidePath=str(outside),
        another=another,
    )

    probe = raster.probe_template(template, context=ctx, width=64, height=32)

    assert probe.errors == (), probe.errors
    assert _Counter.hits == []
    assert any("huge.png" in b for b in probe.blocked)
    assert any("127.0.0.1" in b for b in probe.blocked)


_LOOPS = {
    "on load": "<script>while (true) {}</script>",
    "in seek": "<script>window.seek = () => { while (true) {} };</script>",
    "on a timer after load": "<script>setTimeout(() => { while (true) {} }, 30);</script>",
}


@pytest.mark.parametrize("where", sorted(_LOOPS))
def test_a_stuck_template_times_out_and_the_next_one_renders(
    tmp_path: Path, raster: ChromiumRasterizer, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    monkeypatch.setattr(look_sandbox, "LOAD_SECONDS", 2.0)
    monkeypatch.setattr(look_sandbox, "CALL_SECONDS", 1.5)
    stuck = tmp_path / "stuck" / "card.html"
    stuck.parent.mkdir()
    stuck.write_text(f"<!doctype html><body>{_LOOPS[where]}<p>x</p></body>", encoding="utf-8")
    fine = tmp_path / "fine" / "card.html"
    fine.parent.mkdir()
    fine.write_text("<!doctype html><body style='background:#0c0'>ok</body>", encoding="utf-8")

    started = time.monotonic()
    with pytest.raises(TemplateTimeoutError):
        raster.render_template(stuck, context=_context(), width=64, height=32)
    assert time.monotonic() - started < 10
    png = raster.render_template(fine, context=_context(), width=64, height=32)
    with Image.open(io.BytesIO(png)) as im:
        assert im.convert("RGB").getpixel((32, 16))[1] > 150


def test_a_probe_reports_a_stuck_template_instead_of_raising(
    tmp_path: Path, raster: ChromiumRasterizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(look_sandbox, "LOAD_SECONDS", 2.0)
    monkeypatch.setattr(look_sandbox, "CALL_SECONDS", 1.5)
    stuck = tmp_path / "stuck" / "card.html"
    stuck.parent.mkdir()
    stuck.write_text(f"<!doctype html><body>{_LOOPS['in seek']}</body>", encoding="utf-8")
    probe = raster.probe_template(stuck, context=_context(), width=64, height=32)
    assert probe.errors and "did not answer" in probe.errors[-1]


def test_an_endless_animation_is_sampled_for_a_minute_at_most(
    tmp_path: Path, raster: ChromiumRasterizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(look_sandbox, "MAX_ANIMATION_SECONDS", 0.5)
    template = tmp_path / "long" / "card.html"
    template.parent.mkdir()
    template.write_text(
        "<!doctype html><body><script>window.duration = () => 1e9; window.seek = () => {};</script></body>",
        encoding="utf-8",
    )
    frames = raster.render_template_frames(
        template, context=_context(), width=16, height=16, fps=10, max_seconds=1e9
    )
    try:
        assert frames.frame_count == 5
    finally:
        frames.close()


def test_a_template_error_is_still_a_template_script_error(
    tmp_path: Path, raster: ChromiumRasterizer
) -> None:
    template = tmp_path / "bad" / "card.html"
    template.parent.mkdir()
    template.write_text(
        "<!doctype html><body><script>window.seek = () => { throw new Error('nope'); };</script>",
        encoding="utf-8",
    )
    with pytest.raises(TemplateScriptError, match="nope"):
        raster.render_template(template, context=_context(), width=16, height=16)
