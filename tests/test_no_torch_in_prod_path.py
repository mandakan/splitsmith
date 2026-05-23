"""Sentinel: production-path modules must not ``import torch`` at module level.

See ``docs/local-slim/05-dev-vs-prod-parity.md``. The slim wheel ships
without torch; if anyone adds a top-level ``import torch`` to
``splitsmith.ensemble.*``, the slim install crashes at startup before
the user has a chance to swap backends.

How the test works
------------------
The subprocess starts with a clean ``sys.modules`` -- no test fixture
or sibling module has had a chance to pull in torch yet. After
importing every prod module, the test asserts ``"torch"`` is still
absent from ``sys.modules``. Lazy imports inside
``_build_*_runtime_torch`` helpers don't fire here because the module
import doesn't call those helpers.

This observe-don't-block design avoids scipy's array-api compatibility
probe, which does ``getattr(sys.modules['torch'], 'Tensor')`` and
breaks when the slot is poisoned with ``None``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Modules that must stay torch-free at import time. The list is
# deliberately narrow: it covers the prod hot path (features, visual,
# api, voters, calibration) and avoids the build / training scripts
# under ``scripts/`` -- those keep torch on purpose.
_PROD_MODULES = (
    "splitsmith.ensemble",
    "splitsmith.ensemble.api",
    "splitsmith.ensemble.backend",
    "splitsmith.ensemble.calibration",
    "splitsmith.ensemble.features",
    "splitsmith.ensemble.visual",
    "splitsmith.ensemble.voters",
    "splitsmith.ensemble.tta",
)


def test_prod_modules_import_without_torch() -> None:
    """No prod module pulls in torch as a transitive side-effect of import."""
    snippet = textwrap.dedent(f"""
        import sys

        modules = {list(_PROD_MODULES)!r}
        for name in modules:
            __import__(name)

        torch_modules = sorted(
            m for m in sys.modules
            if m == "torch" or m.startswith("torch.")
        )
        if torch_modules:
            raise SystemExit(
                "splitsmith.ensemble.* pulled in torch at import time: "
                + ", ".join(torch_modules)
            )
        """)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "splitsmith.ensemble.* must not import torch at module top level.\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
