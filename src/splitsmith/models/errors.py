"""Typed failure modes for the slim runtime model layer (doc 03).

The CLI and FastAPI layers branch on these types to render actionable
user messages: "connect to the internet", "upgrade your wheel",
"report this as a bug". Raising a stringly-typed RuntimeError would
force the UI to grep the message, which we don't want.
"""

from __future__ import annotations


class ModelError(RuntimeError):
    """Base for every typed failure raised by ``splitsmith.models``."""


class NetworkUnreachable(ModelError):
    """The host could not be reached at all (DNS / TCP / TLS failure).

    User-facing message advises checking connectivity and re-running
    ``splitsmith fetch-models``. Network errors get a single
    exponential-backoff retry before this fires (see ``download.py``).
    """


class HttpError(ModelError):
    """The server returned a non-success HTTP status.

    The status code is preserved as ``self.status_code`` so the UI
    layer can show "404 -- artifact moved" or "503 -- model server is
    down" without re-parsing the message.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class HashMismatch(ModelError):
    """The downloaded bytes did not hash to the expected SHA256.

    This is a security-meaningful failure. The cache file is removed
    and the runtime aborts; we do not silently retry, because a
    mid-flight bit flip and a malicious mirror look identical at this
    layer.
    """

    def __init__(self, message: str, *, expected: str, actual: str) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual
