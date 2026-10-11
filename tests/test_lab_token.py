"""The lab token gate: a LAN-bound lab answers only the holder of its token."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from splitsmith.ui import lab_token
from splitsmith.ui.server import create_app

TOKEN = "s3cret-token"


def _client(token: str | None) -> TestClient:
    app = create_app(lab_enabled=True, lab_token=token)
    return TestClient(app, follow_redirects=False)


def test_without_token_every_path_is_an_opaque_404() -> None:
    c = _client(TOKEN)
    for path in ("/api/health", "/dev/review", "/api/fixture/audio?path=/etc/hosts", "/"):
        r = c.get(path)
        assert r.status_code == 404, path
        assert "localhost" not in r.text


def test_wrong_token_is_404() -> None:
    c = _client(TOKEN)
    assert c.get("/api/health", params={"lab_token": "nope"}).status_code == 404
    c.cookies.set(lab_token.COOKIE, "nope")
    assert c.get("/api/health").status_code == 404


def test_token_in_the_url_sets_the_cookie_and_redirects_without_it() -> None:
    c = _client(TOKEN)
    r = c.get("/dev/review", params={"lab_token": TOKEN, "phone": "1"})
    assert r.status_code == 303
    assert r.headers["location"] == "/dev/review?phone=1"
    cookie = r.headers["set-cookie"]
    assert f"{lab_token.COOKIE}={TOKEN}" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert c.get("/api/health").status_code == 200


def test_no_token_configured_changes_nothing() -> None:
    c = _client(None)
    assert c.get("/api/health").status_code == 200


@pytest.mark.parametrize(
    ("host", "loop"),
    [("127.0.0.1", True), ("localhost", True), ("::1", True), ("192.168.1.20", False), ("0.0.0.0", False)],
)
def test_is_loopback(host: str, loop: bool) -> None:
    assert lab_token.is_loopback(host) is loop


def test_ui_refuses_a_lan_bind_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import cli

    called: list[dict] = []
    monkeypatch.setattr("splitsmith.ui.server.serve", lambda **kw: called.append(kw))
    monkeypatch.delenv("SPLITSMITH_LAB_TOKEN", raising=False)
    r = CliRunner().invoke(cli.app, ["ui", "--lab", "--no-browser", "--host", "192.168.1.20"])
    assert r.exit_code != 0
    assert "--lab-token" in r.output
    assert called == []
    r = CliRunner().invoke(
        cli.app, ["ui", "--lab", "--no-browser", "--host", "192.168.1.20", "--lab-token", TOKEN]
    )
    assert r.exit_code == 0, r.output
    assert called[0]["lab_token"] == TOKEN
