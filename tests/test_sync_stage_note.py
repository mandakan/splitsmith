"""The stage note across a desktop upgrade (#1376), end to end through the
real ``run_sync`` / ``run_push`` / ``_apply_pull`` against a hosted app.

An install older than ``stage_note`` never sends the key. Hosted keeps a
note written on the mirror when such a push lands (``put_audit_doc``), but
the old desktop records that version as seen, and ``plan_pull`` compares
versions only. Two parts close it: the PUT says ``kept_fields`` (a new
client does not record that version), and a one-time migration forgets the
version of every audit doc whose base predates the field, so the upgraded
desktop pulls those docs once. Adapted from the review's simulation.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC
from pathlib import Path

import httpx
import pytest

pytest.importorskip("moto")
from fastapi.testclient import TestClient  # noqa: E402

from splitsmith import match_model  # noqa: E402
from splitsmith.sync import merge as merge_mod  # noqa: E402
from splitsmith.sync import run as run_mod  # noqa: E402
from splitsmith.sync.client import DocPutResult, HostedSyncClient  # noqa: E402
from splitsmith.sync.run import run_sync  # noqa: E402
from splitsmith.sync.state import load_sync_state, save_sync_state  # noqa: E402

from .hosted_helpers import _CapturingSender, login, moto_s3_storage  # noqa: E402
from .test_sync_integration import BUCKET, SLUG, _build_local_match, _media_handler  # noqa: E402


@pytest.fixture
def hosted(hosted_env: str, monkeypatch: pytest.MonkeyPatch):
    """``test_sync_integration``'s ``hosted``: the hosted
    app over a moto-backed ``S3Storage``."""
    from splitsmith.ui.server import create_app

    with moto_s3_storage(monkeypatch, BUCKET) as captured:
        app = create_app()
        sender = _CapturingSender()
        app.state.splitsmith_state.auth.backends[0]._email = sender
        with TestClient(app, follow_redirects=False) as client:
            yield client, sender, captured


_REAL_MERGE = merge_mod.merge_audit_doc
_REAL_FORGET = run_mod._forget_unaware_audit_versions


def _merge(base, local, remote, **kw):
    # SQLite hands back naive stamps; the merge compares aware ones.
    if kw["remote_ts"].tzinfo is None:
        kw["remote_ts"] = kw["remote_ts"].replace(tzinfo=UTC)
    return _REAL_MERGE(base, local, remote, **kw)


def _old_merge(base, local, remote, **kw):
    """A desktop older than #1376: no stage_note unit, local's doc stands."""
    res = _merge(base, local, remote, **kw)
    if "stage_note" in local:
        res.doc["stage_note"] = local["stage_note"]
    else:
        res.doc.pop("stage_note", None)
    res.conflicts = [c for c in res.conflicts if c.unit != "stage_note"]
    res.changed_vs_local = res.doc != local
    return res


class _Desktop:
    """One desktop install that can be switched from old to upgraded."""

    def __init__(self, hosted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client, sender, captured = hosted
        self.mp = monkeypatch
        self.hosted = client
        self.root, _, _ = _build_local_match(tmp_path)
        login(client, sender, "sim@example.com")
        client.get("/api/me/recent-projects")
        token = client.post("/api/me/desktop-tokens", json={"name": "box"}).json()["token"]
        http = TestClient(
            client.app,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
        media = httpx.Client(transport=httpx.MockTransport(_media_handler(captured["storage"])))
        self.sc = HostedSyncClient(http=http, media_http=media)
        self._real_put = self.sc.put_doc_detail
        self.upgraded()
        run_sync(self.root, client=self.sc)
        self.mid = match_model.Match.load(self.root).match_id
        self.audit = self.root / "shooters" / SLUG / "audit" / "stage1.json"
        self.old = False

    def old_install(self) -> None:
        """Merge without the unit, record every PUT's version, no migration,
        and a sync_state written at schema 2, as the released desktop did."""
        self.old = True
        self.mp.setattr(run_mod, "merge_audit_doc", _old_merge)
        self.mp.setattr(run_mod, "_forget_unaware_audit_versions", lambda root, state: 0)
        real = self._real_put

        def put(match_id, item, *, expected_version):
            return DocPutResult(version=real(match_id, item, expected_version=expected_version).version)

        self.mp.setattr(self.sc, "put_doc_detail", put)

    def upgraded(self) -> None:
        self.old = False
        self.mp.setattr(run_mod, "merge_audit_doc", _merge)
        self.mp.setattr(run_mod, "_forget_unaware_audit_versions", _REAL_FORGET)
        self.mp.setattr(self.sc, "put_doc_detail", self._real_put)

    def sync(self):
        report = run_sync(self.root, client=self.sc)
        if self.old:
            state = load_sync_state(self.root)
            state.schema_version = 2
            save_sync_state(self.root, state)
        return report

    def local(self) -> dict:
        return json.loads(self.audit.read_text())

    def edit(self, **fields) -> None:
        time.sleep(0.01)
        doc = self.local()
        doc.update(fields)
        self.audit.write_text(json.dumps(doc))
        now = time.time()
        os.utime(self.audit, (now, now))

    def hosted_doc(self) -> dict:
        return self.hosted.get(f"/api/matches/{self.mid}/shooters/{SLUG}/stages/1/audit").json()

    def hosted_note(self, text: str) -> None:
        r = self.hosted.patch(
            f"/api/matches/{self.mid}/shooters/{SLUG}/stages/1/stage-note", json={"stage_note": text}
        )
        assert r.status_code == 200, r.text

    def report(self, tag: str, rep) -> str:
        line = (
            f"[{tag}] pulled={rep.pulled} docs={rep.docs} "
            f"conflicts={[c['unit'] for c in rep.conflicts]} "
            f"hosted={self.hosted_doc().get('stage_note', '<absent>')!r} "
            f"local={self.local().get('stage_note', '<absent>')!r}"
        )
        print(line)
        return line


def _old_desktop_pushes_over_a_hosted_note(d: _Desktop) -> None:
    d.old_install()
    d.hosted_note("X")
    d.edit(detection="edit1")
    d.report("old-push", d.sync())
    assert d.hosted_doc()["stage_note"] == "X"  # the PUT kept it
    assert "stage_note" not in d.local()  # the old desktop never saw it
    d.edit(detection="edit2")
    d.report("old-push-2", d.sync())
    assert load_sync_state(d.root).schema_version == 2


def test_an_upgraded_desktop_gets_the_hosted_note_on_its_first_sync(hosted, tmp_path, monkeypatch):
    d = _Desktop(hosted, tmp_path, monkeypatch)
    _old_desktop_pushes_over_a_hosted_note(d)

    d.upgraded()
    rep = d.sync()
    d.report("c-first-upgraded-sync", rep)
    assert rep.pulled >= 1
    assert d.local()["stage_note"] == "X"
    assert d.hosted_doc()["stage_note"] == "X"
    # The migration ran once: the file is at the current schema and the
    # next sync pulls nothing.
    assert load_sync_state(d.root).schema_version == 3
    again = d.sync()
    d.report("c-second", again)
    assert again.pulled == 0


def test_an_upgraded_desktops_own_note_conflicts_with_the_hosted_one_instead_of_overwriting_it(
    hosted, tmp_path, monkeypatch
):
    d = _Desktop(hosted, tmp_path, monkeypatch)
    _old_desktop_pushes_over_a_hosted_note(d)

    d.upgraded()
    d.edit(stage_note="D")
    rep = d.sync()
    d.report("lost-update", rep)
    # Not a silent overwrite: the two notes met in the merge and the
    # conflict is surfaced; the newer (the desktop's, just written) wins on
    # both sides.
    assert [c["unit"] for c in rep.conflicts] == ["stage_note"]
    assert d.local()["stage_note"] == "D" and d.hosted_doc()["stage_note"] == "D"


def _old_desktop_pulls_a_hosted_note_without_pushing(d: _Desktop, monkeypatch: pytest.MonkeyPatch) -> None:
    """The poisoned base: the old desktop pulls the doc with the note,
    records it as base, keeps its own doc without the key, and pushes
    nothing (here: it dies between the pull and the push)."""
    d.old_install()
    d.hosted_note("X")

    def crash(*a, **kw):
        raise RuntimeError("desktop quit mid-sync")

    with monkeypatch.context() as m:
        m.setattr(run_mod, "run_push", crash)
        with pytest.raises(RuntimeError):
            d.sync()
    state = load_sync_state(d.root)
    state.schema_version = 2
    save_sync_state(d.root, state)
    from splitsmith.sync.base import load_base_doc

    assert load_base_doc(d.root, "audit/alice/1")["stage_note"] == "X"
    assert "stage_note" not in d.local()


def test_a_poisoned_base_heals_on_the_first_upgraded_sync(hosted, tmp_path, monkeypatch):
    d = _Desktop(hosted, tmp_path, monkeypatch)
    _old_desktop_pulls_a_hosted_note_without_pushing(d, monkeypatch)
    d.upgraded()
    rep = d.sync()
    d.report("poisoned-first-sync", rep)
    assert rep.pulled == 1
    assert d.local()["stage_note"] == "X" and d.hosted_doc()["stage_note"] == "X"


def test_a_poisoned_base_turns_a_local_note_into_a_conflict_not_an_overwrite(hosted, tmp_path, monkeypatch):
    d = _Desktop(hosted, tmp_path, monkeypatch)
    _old_desktop_pulls_a_hosted_note_without_pushing(d, monkeypatch)
    d.upgraded()
    d.edit(stage_note="D")
    rep = d.sync()
    d.report("poisoned-lost-update", rep)
    assert [c["unit"] for c in rep.conflicts] == ["stage_note"]
    assert d.local()["stage_note"] == d.hosted_doc()["stage_note"] == "D"


def test_kept_fields_make_the_next_sync_pull_without_the_migration(hosted, tmp_path, monkeypatch):
    """Part 1 alone: a desktop already at the current schema pushes a body
    without the key over a hosted note; hosted says ``kept_fields`` and the
    desktop leaves that version unseen, so its next sync pulls the note."""
    d = _Desktop(hosted, tmp_path, monkeypatch)
    _old_desktop_pushes_over_a_hosted_note(d)
    # Upgraded, but with the migration already done (schema 3), so only the
    # PUT's kept_fields can bring the note over.
    state = load_sync_state(d.root)
    state.schema_version = 3
    save_sync_state(d.root, state)
    d.upgraded()
    d.edit(detection="edit3")
    rep = d.sync()
    d.report("kept-push", rep)
    assert "stage_note" not in d.local()
    assert "audit/alice/1" not in load_sync_state(d.root).doc_versions
    rep = d.sync()
    d.report("kept-next-sync", rep)
    assert rep.pulled == 1
    assert d.local()["stage_note"] == "X"


def test_the_migration_reruns_after_a_failed_sync_and_stops_after_a_completed_one(
    hosted, tmp_path, monkeypatch
):
    d = _Desktop(hosted, tmp_path, monkeypatch)
    _old_desktop_pushes_over_a_hosted_note(d)
    d.upgraded()

    calls: list[int] = []

    def counting(root, state):
        calls.append(1)
        return _REAL_FORGET(root, state)

    monkeypatch.setattr(run_mod, "_forget_unaware_audit_versions", counting)
    real_manifest = d.sc.get_doc_manifest

    def boom(match_id):
        raise run_mod.SyncClientError("network down")

    monkeypatch.setattr(d.sc, "get_doc_manifest", boom)
    with pytest.raises(run_mod.SyncClientError):
        d.sync()
    assert calls == [1]
    assert load_sync_state(d.root).schema_version == 2  # not marked done

    monkeypatch.setattr(d.sc, "get_doc_manifest", real_manifest)
    rep = d.sync()
    d.report("migration-after-failure", rep)
    assert calls == [1, 1]
    assert d.local()["stage_note"] == "X"
    assert load_sync_state(d.root).schema_version == 3
    d.sync()
    assert calls == [1, 1]  # never again


def test_an_old_desktop_is_unaffected(hosted, tmp_path, monkeypatch):
    """The released desktop ignores kept_fields and keeps working: its
    pushes go through, it never loses the hosted note, and it keeps its own
    doc as it was."""
    d = _Desktop(hosted, tmp_path, monkeypatch)
    _old_desktop_pushes_over_a_hosted_note(d)
    d.edit(detection="edit3")
    rep = d.sync()
    d.report("old-unaffected", rep)
    assert rep.docs >= 1
    assert d.hosted_doc()["stage_note"] == "X"
    assert d.local()["detection"] == "edit3"
