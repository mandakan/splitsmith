# PyInstaller spec for splitsmith-server (issue #371).
#
# Produces a standalone sidecar binary that the closed-source desktop
# shell from issue #129 spawns as a child process. The OSS bundle does
# NOT include ffmpeg / ffprobe (license complexity + size); the shell
# repo layers them on top, or users install via package manager. The
# binary discovers ffmpeg next to itself at runtime via the lookup
# added in issue #370.
#
# Local invocation (slow -- 15-30 min, ~1-2 GB output):
#     uv run pyinstaller packaging/splitsmith-server.spec
#
# Per-platform release artifacts come out of .github/workflows/release.yml
# on every ``v*`` tag push.

# ruff: noqa
# PyInstaller injects the analysis builtins at parse time; static analyzers
# treat this file as a plain Python module and flag the implicit names.

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

REPO_ROOT = Path(SPECPATH).resolve().parent
ENTRY = REPO_ROOT / "src" / "splitsmith" / "ui" / "embedded.py"
DATA_DIR = REPO_ROOT / "src" / "splitsmith" / "data"
SPA_DIST = REPO_ROOT / "src" / "splitsmith" / "ui_static" / "dist"
TEMPLATES_DIR = REPO_ROOT / "src" / "splitsmith" / "templates"

if not SPA_DIST.exists():
    raise SystemExit(
        f"SPA dist not found at {SPA_DIST}. "
        "Run `npm --prefix src/splitsmith/ui_static run build` first."
    )

# Bundle the shipped ensemble artifacts, fonts, overlay templates, and
# the built SPA assets at their package-relative paths. PyInstaller
# preserves the second element of each tuple as the path inside the
# bundle, matching how splitsmith.runtime + importlib.resources read
# them at runtime.
datas: list[tuple[str, str]] = [
    (str(DATA_DIR), "splitsmith/data"),
    (str(SPA_DIST), "splitsmith/ui_static/dist"),
]
if TEMPLATES_DIR.exists():
    datas.append((str(TEMPLATES_DIR), "splitsmith/templates"))

# Scientific Python rarely auto-detects cleanly: pull every submodule
# we touch so the analyzer doesn't drop one we lazy-import. Heavy but
# avoids the "works in dev, fails on packaged" trap.
hiddenimports: list[str] = []
for package in (
    "sklearn",
    "transformers",
    "panns_inference",
    "librosa",
    "soundfile",
    "mcp",
    "uvicorn",
    "fastapi",
    "pydantic",
    "pydantic_settings",
):
    hiddenimports.extend(collect_submodules(package))

# Some packages ship data files (model configs, vocabularies) that need
# to land beside the import. collect_data_files surfaces those.
for package in ("transformers", "sklearn", "librosa", "panns_inference"):
    datas.extend(collect_data_files(package))

# torch ships its own libs + datas; let PyInstaller's torch hook do the
# heavy lifting (avoids over-collecting which inflates the bundle).
# Adding submodules selectively rather than the whole tree.
hiddenimports.extend(
    [
        "torch",
        "torch.nn",
        "torch.nn.functional",
        "torch._C",
    ]
)

# Excludes: dev-only deps (the wheel resolves them; the bundled sidecar
# never imports them) and the bundler itself.
excludes: list[str] = [
    "matplotlib",
    "pyarrow",
    "PyInstaller",
    "tests",
    "pytest",
    "respx",
    "mypy",
    "ruff",
    "black",
]


a = Analysis(
    [str(ENTRY)],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="splitsmith-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# onedir layout: gives the closed-source shell a directory it can ship
# alongside its own assets (ffmpeg, app icon, etc.) and pin via the
# bundled-binary discovery added in issue #370. onefile mode is slower
# to start (extracts to a temp dir on every launch) and produces worse
# error messages when a hidden import is missing -- not worth the
# single-file convenience here.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="splitsmith-server",
)
