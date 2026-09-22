"""Desktop audit-doc writes under concurrency (#931).

Local mode's audit accessors report and ignore version 0, so the hosted
optimistic-lock retry cannot protect a desktop read-modify-write. These
pin the two replacements: a per-writer temp name plus a lock across the
write and ``.bak`` rotation, and ``_save_audit_with_remerge`` merging onto
the doc on disk rather than the one its caller loaded minutes earlier.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

from splitsmith.ui import server as server_mod

from .test_ui_server import _seed_match_export_project


@contextmanager
def _match_context(project_root: Path):
    """Bind the ContextVar the alias middleware sets per request (local mode)."""
    token = server_mod.current_match_root.set(project_root)
    try:
        yield
    finally:
        server_mod.current_match_root.reset(token)


def test_remerge_on_desktop_keeps_an_edit_saved_after_the_caller_loaded(tmp_path: Path) -> None:
    """A shot-detect job loads the audit, runs for a while, then saves its
    merge. A manual edit saved in between must survive: hosted gets that
    from the version-conflict retry, desktop never raises a conflict, so
    the merge has to land on what is on disk now."""
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state

    with _match_context(project_root):
        stale, version = state.load_audit("me", 1)
        assert stale is not None
        edited = json.loads(json.dumps(stale))
        edited["shots"] = [{"shot_number": 1, "time": 1.23, "source": "manual"}]
        state.save_audit("me", 1, edited, version=version)

        def merge(doc: dict) -> dict:
            doc.setdefault("audit_events", []).append({"kind": "shot_detect_run"})
            return doc

        server_mod._save_audit_with_remerge(
            state, "me", 1, doc=stale, version=version, merge=merge, default=lambda: {"shots": []}
        )
        saved, _ = state.load_audit("me", 1)

    assert saved["shots"] == [{"shot_number": 1, "time": 1.23, "source": "manual"}]
    assert saved["audit_events"][-1] == {"kind": "shot_detect_run"}


def test_two_concurrent_desktop_audit_writers_both_succeed(tmp_path: Path, monkeypatch) -> None:
    """``save_audit`` is safe to call from two threads at once.

    ``Path.replace`` on the audit's own files waits for the other writer
    to arrive too, so without serialisation both writers are inside the
    rotate at once: with a shared ``stage1.json.tmp`` and an unlocked
    ``.bak`` rotation the second ``replace`` finds its source gone and the
    save fails with a 500. Under the lock the second writer never arrives
    while the first waits; the barrier times out, the first proceeds, and
    both saves land in turn.
    """
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state

    real_replace = Path.replace
    both_inside = threading.Barrier(2)

    def gated_replace(self: Path, target):  # type: ignore[no-untyped-def]
        if self.name.startswith("stage1.json"):
            try:
                both_inside.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                pass
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", gated_replace, raising=True)

    errors: list[BaseException] = []

    def writer(n: int) -> None:
        with _match_context(project_root):
            try:
                state.save_audit("me", 1, {"shots": [], "writer": n}, version=0)
            except BaseException as exc:  # noqa: BLE001 -- reported, not swallowed
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == [], f"concurrent save_audit raised: {errors!r}"
    audit_dir = project_root / "shooters" / "me" / "audit"
    assert json.loads((audit_dir / "stage1.json").read_text())["writer"] in (1, 2)
    assert (audit_dir / "stage1.json.bak").exists()
    assert not list(audit_dir.glob("*.tmp"))
