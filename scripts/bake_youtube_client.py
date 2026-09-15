"""Write the YouTube OAuth client into ``youtube/oauth.py`` at publish time.

The splitsmith Google Cloud project's Desktop client is what every
installed copy logs in with. Google treats an installed app's client
secret as non-confidential (PKCE and the loopback redirect carry the
flow's safety), but GitHub's push protection and Google's own scanners
still block or flag a Google client secret in a public repository. So
the constants stay empty in git and the publish workflow bakes them into
the wheel from repo secrets, right before ``uv build``.

Usage (CI)::

    SPLITSMITH_YOUTUBE_CLIENT_ID=... SPLITSMITH_YOUTUBE_CLIENT_SECRET=... \\
        python scripts/bake_youtube_client.py [path/to/oauth.py]

Exits 2 when either variable is unset: a wheel that ships without the
client would break "Connect YouTube" for everyone who installs it, so a
release must fail loudly rather than publish that.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ENV_ID = "SPLITSMITH_YOUTUBE_CLIENT_ID"
ENV_SECRET = "SPLITSMITH_YOUTUBE_CLIENT_SECRET"
DEFAULT_TARGET = Path(__file__).resolve().parent.parent / "src" / "splitsmith" / "youtube" / "oauth.py"

_ID_LINE = re.compile(r'^BUILTIN_CLIENT_ID = ".*"$', re.MULTILINE)
_SECRET_LINE = re.compile(r'^BUILTIN_CLIENT_SECRET = ".*"$', re.MULTILINE)


def bake(source: str, *, client_id: str, client_secret: str) -> str:
    """Return ``source`` with the two constants set. Raises ``ValueError``
    when either line is missing, so a refactor of ``oauth.py`` that moves
    them fails the release instead of shipping an empty client."""
    for value, name in ((client_id, "client id"), (client_secret, "client secret")):
        if not value or '"' in value or "\n" in value:
            raise ValueError(f"{name} is empty or not a plain string")
    if _ID_LINE.search(source) is None or _SECRET_LINE.search(source) is None:
        raise ValueError("BUILTIN_CLIENT_ID / BUILTIN_CLIENT_SECRET lines not found")
    out = _ID_LINE.sub(f'BUILTIN_CLIENT_ID = "{client_id}"', source, count=1)
    return _SECRET_LINE.sub(f'BUILTIN_CLIENT_SECRET = "{client_secret}"', out, count=1)


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else DEFAULT_TARGET
    client_id = os.environ.get(ENV_ID, "")
    client_secret = os.environ.get(ENV_SECRET, "")
    if not client_id or not client_secret:
        print(f"error: {ENV_ID} and {ENV_SECRET} must both be set", file=sys.stderr)
        return 2
    try:
        baked = bake(target.read_text(encoding="utf-8"), client_id=client_id, client_secret=client_secret)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    target.write_text(baked, encoding="utf-8")
    print(f"baked YouTube client {client_id[:12]}... into {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
