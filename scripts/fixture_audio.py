"""Fixture audio kept out of git: content-addressed in R2, versioned by a manifest.

``tests/fixtures/audio.lock.json`` maps each externally stored fixture WAV to
its sha256 and size; the WAV lives at ``wav/<sha256>.wav`` in a private
bucket, and ``tests/fixtures/.gitignore`` keeps the local copy out of git.
A changed WAV is a new object under a new hash, so the manifest in git is the
whole version history and nothing in the bucket is ever overwritten.

    uv run python scripts/fixture_audio.py fetch    # download what is missing, verify every hash
    uv run python scripts/fixture_audio.py fetch --only tests/fixtures/audio.ci.txt  # just the WAVs CI reads
    uv run python scripts/fixture_audio.py status   # what is local, missing or changed
    uv run python scripts/fixture_audio.py push STEM_OR_WAV ...  # upload, then record in the manifest

``fetch`` reads ``SPLITSMITH_FIXTURE_AUDIO_URL`` (default: the bucket's domain).
The domain sits behind Cloudflare Access: the corpus holds other shooters'
recordings, so it is not public until each clip has had a speech-masking review.
Requests carry an Access service token from ``SPLITSMITH_FIXTURE_AUDIO_CLIENT_ID``
and ``SPLITSMITH_FIXTURE_AUDIO_CLIENT_SECRET`` (CI has them as secrets; a fork's
pull request does not, so its fixture-audio tests skip). ``push`` uploads with
``wrangler r2 object put`` into ``splitsmith-fixtures``, so it needs a
``wrangler login`` on the Cloudflare account that owns splitsmith.app; it skips
an object the bucket already serves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
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
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]


def save_manifest(files: dict[str, dict]) -> None:
    body = {"version": 1, "files": dict(sorted(files.items()))}
    MANIFEST.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def write_gitignore(names: list[str]) -> None:
    """Rewrite the managed block of ``tests/fixtures/.gitignore``; keep the rest."""
    kept: list[str] = []
    inside = False
    if GITIGNORE.exists():
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines():
            if line == _BEGIN:
                inside = True
            elif line == _END:
                inside = False
            elif not inside:
                kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    block = [_BEGIN, *[f"/{n}" for n in sorted(names)], _END]
    GITIGNORE.write_text("\n".join([*kept, *([""] if kept else []), *block]) + "\n", encoding="utf-8")


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


CLIENT_ID_ENV = "SPLITSMITH_FIXTURE_AUDIO_CLIENT_ID"
CLIENT_SECRET_ENV = "SPLITSMITH_FIXTURE_AUDIO_CLIENT_SECRET"


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    # Cloudflare answers Python's default User-Agent with 403.
    headers = {"User-Agent": "splitsmith-fixture-audio/1"}
    client_id, secret = os.environ.get(CLIENT_ID_ENV), os.environ.get(CLIENT_SECRET_ENV)
    if client_id and secret:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = secret
    return urllib.request.Request(url, method=method, headers=headers)


def _explain(exc: OSError) -> str:
    """A refusal from Access reads as a missing token, not a network fault."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
        if not (os.environ.get(CLIENT_ID_ENV) and os.environ.get(CLIENT_SECRET_ENV)):
            return f"{exc} (fixture audio is private: set {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV})"
    return str(exc)


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
        return f"{name}: {_explain(exc)}"
    finally:
        tmp.unlink(missing_ok=True)


def read_names(path: Path) -> list[str]:
    """A names file: one WAV per line (``.wav`` optional), ``#`` comments and blanks ignored."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            out.append(name if name.endswith(".wav") else f"{name}.wav")
    return out


def select(files: dict[str, dict], only: list[str] | None) -> tuple[dict[str, dict], list[str]]:
    """The manifest entries ``only`` names (all of them when ``None``), and the
    names it asks for that the manifest does not hold."""
    if only is None:
        return files, []
    return {n: files[n] for n in only if n in files}, [n for n in only if n not in files]


def fetch(base_url: str, workers: int = 8, only: list[str] | None = None) -> list[str]:
    """Download every manifest WAV (or every one ``only`` names) that is missing
    or differs; return the errors. A name the manifest lacks is an error."""
    files, unknown = select(load_manifest(), only)
    st = status(files)
    todo = st["missing"] + st["changed"]
    with ThreadPoolExecutor(workers) as pool:
        errors = list(pool.map(lambda n: _download(base_url, n, files[n]), todo))
    print(f"{len(st['ok'])} present, {len(todo)} fetched")
    return [f"{n}: not in {MANIFEST.name}" for n in unknown] + [e for e in errors if e]


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
    subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8")


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
    f.add_argument("--only", type=Path, help="fetch only the WAVs this names file lists (one per line)")
    sub.add_parser("status")
    p = sub.add_parser("push")
    p.add_argument("names", nargs="+")
    p.add_argument("--url", default=os.environ.get("SPLITSMITH_FIXTURE_AUDIO_URL", DEFAULT_URL))
    args = ap.parse_args()
    if args.cmd == "fetch":
        errors = fetch(args.url, only=read_names(args.only) if args.only else None)
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
