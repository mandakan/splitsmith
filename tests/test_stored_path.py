"""A stored path is written with forward slashes on every platform (``splitsmith.stored_path``)."""

from __future__ import annotations

import json
from pathlib import PureWindowsPath

from splitsmith.match_project import StageVideo


def test_a_windows_path_is_stored_with_forward_slashes() -> None:
    # model_construct keeps the PureWindowsPath a Windows install would hold,
    # so this runs the serializer on any host.
    video = StageVideo.model_construct(path=PureWindowsPath(r"raw\VID.mp4"), role="primary")

    assert json.loads(video.model_dump_json(include={"path"})) == {"path": "raw/VID.mp4"}
    assert video.model_dump(mode="json", include={"path"}) == {"path": "raw/VID.mp4"}


def test_python_mode_keeps_the_path_object() -> None:
    video = StageVideo.model_construct(path=PureWindowsPath(r"raw\VID.mp4"), role="primary")

    assert video.model_dump(include={"path"}) == {"path": PureWindowsPath(r"raw\VID.mp4")}
