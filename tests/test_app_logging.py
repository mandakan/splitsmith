"""The hosted serve path must surface ``splitsmith.*`` INFO logs.

Regression guard for the bug where the console magic-link line was
silently dropped in deployed containers: ``uvicorn.Config(log_level=
"info")`` configures only uvicorn's loggers, so the app's INFO records
propagated to a root logger left at WARNING and vanished. The whole point
of the console e-mail backend is that the operator can read the sign-in
link from the logs, so this is the test that proves they can.
"""

from __future__ import annotations

import asyncio
import io
import logging

from splitsmith.db.email import CONSOLE_MAGIC_LINK_MARKER, ConsoleEmailSender
from splitsmith.ui.server import _configure_app_logging


def _reset_pkg_logger() -> logging.Logger:
    pkg = logging.getLogger("splitsmith")
    # Drop any stdout handler a prior call/import attached so this test
    # binds its own stream rather than hitting the idempotence guard, and
    # restore the level so we don't leak INFO capture into other tests.
    pkg.handlers = [h for h in pkg.handlers if not getattr(h, "_splitsmith_stdout", False)]
    pkg.setLevel(logging.NOTSET)
    return pkg


def test_configure_app_logging_surfaces_console_magic_link() -> None:
    _reset_pkg_logger()
    buf = io.StringIO()
    try:
        _configure_app_logging(stream=buf)
        link = "https://my.staging.splitsmith.app/auth/callback?token=SEKRIT123"
        asyncio.run(ConsoleEmailSender().send_magic_link(to="m@thias.se", link=link))
        out = buf.getvalue()
        assert CONSOLE_MAGIC_LINK_MARKER in out, out
        assert "token=SEKRIT123" in out, out
        assert "m@thias.se" in out, out
    finally:
        _reset_pkg_logger()


def test_configure_app_logging_is_idempotent() -> None:
    pkg = _reset_pkg_logger()
    try:
        _configure_app_logging(stream=io.StringIO())
        _configure_app_logging(stream=io.StringIO())
        marked = [h for h in pkg.handlers if getattr(h, "_splitsmith_stdout", False)]
        assert len(marked) == 1  # second call is a no-op, not a duplicate handler
    finally:
        _reset_pkg_logger()
