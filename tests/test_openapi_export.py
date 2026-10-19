"""The committed OpenAPI document matches the routes.

``src/splitsmith/ui_static/openapi.json`` is what the SPA's ``pnpm gen:api``
turns into ``types.gen.ts``; a route or model change that is not exported
would leave the generated types describing the old server. Regenerate with
``uv run python scripts/export_openapi.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_openapi  # noqa: E402


def test_the_committed_openapi_document_is_current() -> None:
    expected = export_openapi.render(export_openapi.build_document())
    actual = export_openapi.OUT.read_text(encoding="utf-8")
    assert (
        actual == expected
    ), "src/splitsmith/ui_static/openapi.json is stale; run: uv run python scripts/export_openapi.py"


def test_the_document_resolves_every_body_model() -> None:
    """A request model defined inside a function cannot be resolved by the
    schema builder (``PromoteFromAnchorBody`` was one); the export would raise."""
    doc = export_openapi.build_document()
    assert doc["components"]["schemas"], "no schemas at all"


def test_a_response_only_schema_has_every_field_required() -> None:
    """``Job`` is only ever a response and ``model_dump`` writes every field,
    so the generated type must not mark its defaulted fields optional."""
    job = export_openapi.build_document()["components"]["schemas"]["Job"]
    assert set(job["required"]) == set(job["properties"])


def test_a_request_body_schema_keeps_input_semantics() -> None:
    """``ExportPresetBody`` is posted by the client, where a default means the
    field may be omitted; it stays as pydantic describes it."""
    body = export_openapi.build_document()["components"]["schemas"]["ExportPresetBody"]
    assert set(body.get("required", [])) != set(body["properties"])
