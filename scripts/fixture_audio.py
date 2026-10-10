"""Fixture audio kept out of git: content-addressed in R2, versioned by a manifest.

``tests/fixtures/audio.lock.json`` maps each externally stored fixture WAV to
its sha256 and size; the WAV lives at ``wav/<sha256>.wav`` in a public-read
bucket, and ``tests/fixtures/.gitignore`` keeps the local copy out of git.
A changed WAV is a new object under a new hash, so the manifest in git is the
whole version history and nothing in the bucket is ever overwritten.

    uv run python scripts/fixture_audio.py fetch    # download what is missing, verify every hash
    uv run python scripts/fixture_audio.py status   # what is local, missing or changed
    uv run python scripts/fixture_audio.py push STEM_OR_WAV ...  # upload, then record in the manifest

``fetch`` reads ``SPLITSMITH_FIXTURE_AUDIO_URL`` (default: the bucket's public
domain) and needs no credentials. ``push`` uploads with ``wrangler r2 object put``
into ``splitsmith-fixtures``, so it needs a ``wrangler login`` on the Cloudflare
account that owns splitsmith.app; it skips an object the public URL already serves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
MANIFEST = FIXTURES / "audio.lock.json"
GITIGNORE = FIXTURES / ".gitignore"
DEFAULT_URL = "https://fixtures.splitsmith.app"
BUCKET = "splitsmith-fixtures"
_BEGIN = "# BEGIN fixture_audio.py: WAVs stored in R2 (audio.lock.json)"
_END = "# END fixture_audio.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key_for(digest: str) -> str:
    return f"wav/{digest}.wav"


def load_manifest() -> dict[str, dict]:
    if not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text())["files"]


def save_manifest(files: dict[str, dict]) -> None:
    body = {"version": 1, "files": dict(sorted(files.items()))}
    MANIFEST.write_text(json.dumps(body, indent=2) + "\n")


def write_gitignore(names: list[str]) -> None:
    """Rewrite the managed block of ``tests/fixtures/.gitignore``; keep the rest."""
    kept: list[str] = []
    inside = False
    if GITIGNORE.exists():
        for line in GITIGNORE.read_text().splitlines():
            if line == _BEGIN:
                inside = True
            elif line == _END:
                inside = False
            elif not inside:
                kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    block = [_BEGIN, *[f"/{n}" for n in sorted(names)], _END]
    GITIGNORE.write_text("\n".join([*kept, *([""] if kept else []), *block]) + "\n")


def status(manifest: dict[str, dict] | None = None) -> dict[str, list[str]]:
    files = load_manifest() if manifest is None else manifest
    out: dict[str, list[str]] = {"ok": [], "missing": [], "changed": []}
    for name, entry in sorted(files.items()):
        p = FIXTURES / name
        if not p.exists():
            out["missing"].append(name)
        elif p.stat().st_size != entry["bytes"] or sha256(p) != entry["sha256"]:
            out["changed"].append(name)
        else:
            out["ok"].append(name)
    return out


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    # Cloudflare answers Python's default User-Agent with 403.
    return urllib.request.Request(url, method=method, headers={"User-Agent": "splitsmith-fixture-audio/1"})


def _download(base_url: str, name: str, entry: dict) -> str | None:
    url = f"{base_url.rstrip('/')}/{key_for(entry['sha256'])}"
    fd, tmp_name = tempfile.mkstemp(dir=FIXTURES, suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as resp, tmp.open("wb") as f:  # noqa: S310
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        if sha256(tmp) != entry["sha256"]:
            return f"{name}: hash mismatch from {url}"
        tmp.replace(FIXTURES / name)
        return None
    except OSError as exc:
        return f"{name}: {exc}"
    finally:
        tmp.unlink(missing_ok=True)


def fetch(base_url: str, workers: int = 8) -> list[str]:
    """Download every manifest WAV that is missing or differs; return the errors."""
    files = load_manifest()
    st = status(files)
    todo = st["missing"] + st["changed"]
    with ThreadPoolExecutor(workers) as pool:
        errors = list(pool.map(lambda n: _download(base_url, n, files[n]), todo))
    print(f"{len(st['ok'])} present, {len(todo)} fetched")
    return [e for e in errors if e]


def _object_exists(base_url: str, digest: str) -> bool:
    try:
        with urllib.request.urlopen(  # noqa: S310
            _request(f"{base_url.rstrip('/')}/{key_for(digest)}", method="HEAD"), timeout=30
        ):
            return True
    except OSError:
        return False


def _upload(path: Path, digest: str) -> None:
    cmd = ["npx", "--yes", "wrangler@4", "r2", "object", "put", f"{BUCKET}/{key_for(digest)}"]
    cmd += ["--file", str(path), "--content-type", "audio/wav", "--remote"]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def push(names: list[str], base_url: str = DEFAULT_URL) -> None:
    """Upload each WAV the bucket lacks, then record it in the manifest."""
    files = load_manifest()
    for raw in names:
        name = Path(raw).name if raw.endswith(".wav") else f"{raw}.wav"
        p = FIXTURES / name
        digest = sha256(p)
        if not _object_exists(base_url, digest):
            _upload(p, digest)
        files[name] = {"sha256": digest, "bytes": p.stat().st_size}
        save_manifest(files)  # after every upload, so an interrupted push loses nothing
        print(f"  {name} -> {key_for(digest)}")
    write_gitignore(list(files))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--url", default=os.environ.get("SPLITSMITH_FIXTURE_AUDIO_URL", DEFAULT_URL))
    sub.add_parser("status")
    p = sub.add_parser("push")
    p.add_argument("names", nargs="+")
    p.add_argument("--url", default=os.environ.get("SPLITSMITH_FIXTURE_AUDIO_URL", DEFAULT_URL))
    args = ap.parse_args()
    if args.cmd == "fetch":
        errors = fetch(args.url)
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return 1 if errors else 0
    if args.cmd == "status":
        st = status()
        for k, v in st.items():
            print(f"{k}: {len(v)}")
            if k != "ok":
                for n in v:
                    print(f"  {n}")
        return 0
    push(args.names, args.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
