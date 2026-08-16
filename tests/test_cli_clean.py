"""Integration tests for ``splitsmith clean`` CLI subcommand."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from splitsmith.cli import app
from splitsmith.match_project import MatchProject, StageEntry, StageVideo


def _seed_project(tmp_path: Path) -> Path:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="CLI Match")

    # A registered stage whose primary source is on disk. Without it every
    # artefact below resolves as unreconstructable (no stage to key on),
    # and the assertions about ordinary categories would be testing the
    # held-back path by accident rather than the deletion they name.
    project.stages.append(
        StageEntry(
            stage_number=1,
            stage_name="one",
            time_seconds=12.5,
            videos=[StageVideo(path=Path("raw/source.mp4"), role="primary", stage_number=1)],
        )
    )
    project.save(root)

    audio = project.audio_path(root)
    audio.mkdir(parents=True, exist_ok=True)
    (audio / "stage1_primary.wav").write_bytes(b"\x00" * 1024)
    (audio / "stage1_primary.peaks-2048.json").write_text("[]")

    trimmed = project.trimmed_path(root)
    trimmed.mkdir(parents=True, exist_ok=True)
    (trimmed / "stage1_trimmed.mp4").write_bytes(b"\x00" * 8192)

    exp = project.exports_path(root)
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "stage1_one.fcpxml").write_text("<fcpxml/>")
    (exp / "stage1_one_overlay.mov").write_bytes(b"\x00" * 4096)
    (exp / "stage1_one_trimmed.mp4").write_bytes(b"\x00" * 4096)

    audit = project.audit_path(root)
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "stage1.json").write_text("{}")

    raw = project.raw_path(root)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "source.mp4").write_bytes(b"\x00" * 16384)

    return root


def test_clean_dry_run_shows_plan_and_deletes_nothing(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["clean", str(root), "--all"])
    assert result.exit_code == 0, result.stdout
    assert "Dry run" in result.stdout
    # All files still present
    assert (root / "audit" / "stage1.json").exists()
    assert (root / "exports" / "stage1_one_overlay.mov").exists()


def test_clean_all_yes_deletes_safe_categories_only(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["clean", str(root), "--all", "--yes"])
    assert result.exit_code == 0, result.stdout

    # Safe categories gone
    assert not (root / "audio" / "stage1_primary.wav").exists()
    assert not (root / "audio" / "stage1_primary.peaks-2048.json").exists()
    assert not (root / "trimmed" / "stage1_trimmed.mp4").exists()
    assert not (root / "exports" / "stage1_one.fcpxml").exists()
    assert not (root / "exports" / "stage1_one_overlay.mov").exists()
    assert not (root / "exports" / "stage1_one_trimmed.mp4").exists()
    # Audit data preserved
    assert (root / "audit" / "stage1.json").exists()
    # Sources untouched
    assert (root / "raw" / "source.mp4").exists()
    assert (root / "project.json").exists()


def test_clean_include_audit_yes_wipes_audit_too(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["clean", str(root), "--all", "--include-audit", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert not (root / "audit" / "stage1.json").exists()
    assert (root / "raw" / "source.mp4").exists()
    assert (root / "project.json").exists()


def test_clean_no_flags_errors(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["clean", str(root)])
    assert result.exit_code != 0
    needle = "select at least one category"
    assert needle in result.stdout.lower() or needle in (result.stderr or "").lower()


def test_clean_not_a_project_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["clean", str(tmp_path / "nope"), "--caches"])
    assert result.exit_code != 0


def _add_unrebuildable_trim(root: Path) -> tuple[Path, str]:
    """Register a second camera whose source is gone and give it a trim.

    Its ``_cam_<id>_`` segment resolves to a registered video, so this is
    not the fail-closed "unknown id" path -- the source really is missing,
    which is the only thing that makes the trim irreplaceable.
    """
    project = MatchProject.load(root)
    secondary = StageVideo(path=Path("raw/gone.mp4"), role="secondary", stage_number=1)
    project.stages[0].videos.append(secondary)
    project.save(root)

    trim = project.exports_path(root) / f"stage1_one_cam_{secondary.video_id}_trimmed.mp4"
    trim.write_bytes(b"\x00" * 65536)
    return trim, trim.name


def test_clean_all_yes_holds_back_a_trim_whose_source_is_gone(tmp_path: Path) -> None:
    """#924: ``reconstructable`` was computed and then never consulted, so
    ``--all --yes`` deleted an irreplaceable file without a word. The SPA
    excludes such items from "select all" and gates them behind an
    explicit opt-in; the two surfaces must not disagree about a data-loss
    decision.
    """
    root = _seed_project(tmp_path)
    trim, name = _add_unrebuildable_trim(root)
    runner = CliRunner()

    result = runner.invoke(app, ["clean", str(root), "--all", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert trim.exists(), "an unrebuildable trim must survive a convenience --all"
    # Rebuildable neighbours in the same category still go.
    assert not (root / "exports" / "stage1_one_trimmed.mp4").exists()
    # And the user is told, by name, what was held back and why.
    assert name in result.stdout
    assert "--include-unrebuildable" in result.stdout


def test_clean_dry_run_names_the_unrebuildable_file(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    _, name = _add_unrebuildable_trim(root)
    runner = CliRunner()

    result = runner.invoke(app, ["clean", str(root), "--all"])

    assert result.exit_code == 0, result.stdout
    # Read the rendered output, not the model: a Rich table cell would
    # ellipsize a filename this long and the assertion would still pass
    # while the user saw nothing (#617).
    assert name in result.stdout
    assert "cannot be rebuilt" in result.stdout.lower()


def test_clean_include_unrebuildable_deletes_it(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    trim, _ = _add_unrebuildable_trim(root)
    runner = CliRunner()

    result = runner.invoke(app, ["clean", str(root), "--all", "--include-unrebuildable", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert not trim.exists()


def test_clean_specific_category_only(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["clean", str(root), "--exports-overlays", "--yes"])
    assert result.exit_code == 0
    # Overlay gone, everything else preserved
    assert not (root / "exports" / "stage1_one_overlay.mov").exists()
    assert (root / "exports" / "stage1_one_trimmed.mp4").exists()
    assert (root / "audio" / "stage1_primary.wav").exists()
