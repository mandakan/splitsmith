"""The anonymous share shell's shooters list makes no storage calls (#1180).

``stages_missing_trim`` only gates the Rebuild button, which the share
shell never renders, so a share request reports 0 and asks storage
nothing. The owner's own request still asks -- through the per-request
listing index, one ``list`` of the raw prefix rather than a HEAD per angle.
Reuses ``test_share_routes``' hosted seed helpers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from splitsmith.storage import S3Storage
from tests.hosted_helpers import _CapturingSender, login, moto_s3_storage, seed_match

from .test_share_routes import (
    MID,
    SLUG,
    _create_share_token,
    _seed_stage_video_and_audit,
    _seed_state_docs,
    _share_url,
)


def test_share_shooters_list_makes_no_storage_calls_and_reports_no_missing_trims(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sender = hosted_app
    with moto_s3_storage(monkeypatch, "share-shooters-no-storage-bucket"):
        login(client, sender, "owner@example.com")
        seed_match(hosted_env, "owner@example.com", MID)
        _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
        _seed_stage_video_and_audit(
            hosted_env,
            "owner@example.com",
            MID,
            SLUG,
            {"stage_number": 1, "beep_time": 5.0, "shots": []},
        )

        calls: list[str] = []
        real_exists, real_list = S3Storage.exists, S3Storage.list

        def _counting_exists(self: S3Storage, path: str) -> bool:
            calls.append(f"exists:{path}")
            return real_exists(self, path)

        def _counting_list(self: S3Storage, prefix: str):  # noqa: ANN202
            calls.append(f"list:{prefix}")
            return real_list(self, prefix)

        monkeypatch.setattr(S3Storage, "exists", _counting_exists)
        monkeypatch.setattr(S3Storage, "list", _counting_list)

        # Owner: the index lists the raw prefix once. The source is absent
        # from storage, so the stage is not rebuildable and counts 0 too;
        # the discriminator between owner and share is the call list. The
        # equality is deliberate: it proves the counting hooks fire (so the
        # share-side ``[]`` below is not vacuous) and it pins that this route
        # stays at one storage call per prefix -- a second one is a regression
        # to argue for, not to absorb.
        owner = client.get(f"/api/matches/{MID}/match/shooters")
        assert owner.status_code == 200, owner.text
        assert calls == ["list:raw/"], calls

        token = _create_share_token(client, MID)
        client.cookies.clear()
        calls.clear()

        resp = client.get(_share_url(token, "match/shooters"))

        assert resp.status_code == 200, resp.text
        (shooter,) = resp.json()["shooters"]
        assert shooter["stages_missing_trim"] == 0
        assert calls == [], calls
