import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "desktop" / "scripts" / "check_youtube_client.py"


def _fake_install(root: Path, client_id: str, client_secret: str) -> Path:
    pkg = root / "splitsmith" / "youtube"
    pkg.mkdir(parents=True)
    (root / "splitsmith" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "oauth.py").write_text(
        "import httpx_that_is_not_installed  # the check must not import the module\n"
        f'BUILTIN_CLIENT_ID = "{client_id}"\n'
        f'BUILTIN_CLIENT_SECRET = "{client_secret}"\n'
    )
    return root


def _run(site: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", str(SCRIPT)],
        env={"PYTHONPATH": str(site)},
        capture_output=True,
        text=True,
    )


def test_a_checkout_wheel_with_empty_constants_fails(tmp_path: Path) -> None:
    r = _run(_fake_install(tmp_path, "", ""))
    assert r.returncode == 1
    assert "BUILTIN_CLIENT_ID" in r.stderr and "BUILTIN_CLIENT_SECRET" in r.stderr


def test_one_empty_constant_fails_and_names_it(tmp_path: Path) -> None:
    r = _run(_fake_install(tmp_path, "id.apps.googleusercontent.com", ""))
    assert r.returncode == 1
    assert "BUILTIN_CLIENT_SECRET" in r.stderr
    assert "BUILTIN_CLIENT_ID," not in r.stderr


def test_a_baked_wheel_passes_without_printing_the_secret(tmp_path: Path) -> None:
    r = _run(_fake_install(tmp_path, "id.apps.googleusercontent.com", "s3cr3t"))
    assert r.returncode == 0, r.stderr
    assert "s3cr3t" not in r.stdout + r.stderr
