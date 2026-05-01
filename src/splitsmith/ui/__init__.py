"""Production UI package for splitsmith.

The UI is a localhost SPA driven by a FastAPI backend that orchestrates the
existing engine modules unchanged. See docs/PRODUCTION_UI.md and issue #11
for the v1 contract.

Sub-packages:
- ``project``: Pydantic models for the on-disk match-project layout
- ``server``: FastAPI app + static asset serving
"""
