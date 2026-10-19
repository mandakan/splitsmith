"""Write the server's OpenAPI document to ``src/splitsmith/ui_static/openapi.json``.

The SPA generates ``src/lib/api/types.gen.ts`` from that file (``pnpm gen:api``),
so the request and response types the pages see are the server's own.
Built offline from ``create_app(lab_enabled=True)``: no server, no browser,
no database. ``tests/test_openapi_export.py`` fails when the committed file
is behind the routes; run this script and commit the result.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "splitsmith" / "ui_static" / "openapi.json"


_REF = re.compile(r'"#/components/schemas/(\w+)"')
_METHODS = ("get", "post", "put", "delete", "patch")


def _reachable(schemas: dict, roots: set[str]) -> set[str]:
    seen = set(roots)
    stack = list(roots)
    while stack:
        for ref in _REF.findall(json.dumps(schemas.get(stack.pop(), {}))):
            if ref not in seen:
                seen.add(ref)
                stack.append(ref)
    return seen


def mark_response_fields_required(doc: dict) -> dict:
    """Make every field of a response-only schema required.

    pydantic's JSON schema leaves a field with a default out of ``required``
    even in serialization mode, so the generated TypeScript would say
    ``acknowledged?: boolean`` for a ``Job`` the server always serializes
    whole. A model that only ever appears in responses is emitted by
    ``model_dump``, which writes every field (``None`` as ``null``), so for
    those schemas "has a default" says nothing about presence. Schemas that
    also appear in a request body keep pydantic's input semantics: there the
    default really does mean the client may omit the field.

    This holds because no response in the app is serialized with
    ``exclude_none`` or ``exclude_unset``; a route that starts doing so must
    name its model in ``_SERIALIZED_SPARSELY`` so it is left alone here.
    """
    schemas = doc.get("components", {}).get("schemas", {})
    in_bodies: set[str] = set()
    in_responses: set[str] = set()
    for item in doc.get("paths", {}).values():
        for method, op in item.items():
            if method not in _METHODS:
                continue
            in_bodies |= set(_REF.findall(json.dumps(op.get("requestBody", {}))))
            in_responses |= set(_REF.findall(json.dumps(op.get("responses", {}))))
    inputs = _reachable(schemas, in_bodies)
    for name in _reachable(schemas, in_responses) - inputs - _SERIALIZED_SPARSELY:
        schema = schemas.get(name, {})
        props = schema.get("properties")
        if props:
            schema["required"] = sorted(props)
    # pydantic marks a computed field ``readOnly``; openapi-typescript turns
    # that into a TypeScript ``readonly`` property, which stops a response
    # being held in the client's own mutable types for no gain: the client
    # never sends these objects back.
    for schema in schemas.values():
        for prop in schema.get("properties", {}).values():
            prop.pop("readOnly", None)
    return doc


#: Response models a handler serializes with ``exclude_none`` or ``exclude_unset``.
_SERIALIZED_SPARSELY: frozenset[str] = frozenset()


def build_document() -> dict:
    """The OpenAPI document of the local-mode app with the lab routes on."""
    os.environ.setdefault("SPLITSMITH_AUTO_SYNC", "0")
    os.environ.setdefault("SPLITSMITH_HOME", tempfile.mkdtemp(prefix="splitsmith-openapi-"))
    from splitsmith.ui.server import create_app

    return mark_response_fields_required(create_app(lab_enabled=True).openapi())


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    text = render(build_document())
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale; run: uv run python scripts/export_openapi.py", file=sys.stderr)
            return 1
        print("openapi.json is current")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
