"""review_session.py's phone helpers."""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("review_session", ROOT / "scripts" / "review_session.py")
assert _spec is not None and _spec.loader is not None
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


def test_phone_token_persists_private_and_rotates(tmp_path: Path) -> None:
    p = tmp_path / "build" / "review_phone_token"
    first = rs.phone_token(p, rotate=False)
    assert len(first) >= 32
    assert rs.phone_token(p, rotate=False) == first
    # POSIX mode bits; Windows has none (it keeps a file under the user's
    # profile private through the profile's ACL), so it checks nothing here.
    if sys.platform != "win32":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert rs.phone_token(p, rotate=True) != first


def test_phone_url_opens_the_queue_with_the_token() -> None:
    assert rs.phone_url("192.168.1.20", 5180, "abc") == "http://192.168.1.20:5180/dev/review?lab_token=abc"


def test_lan_address_is_not_loopback() -> None:
    assert not rs.lan_address().startswith("127.")
