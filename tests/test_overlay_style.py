"""The overlay style on request bodies and presets (template HUD, slice 3)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from splitsmith.export_presets import ExportPresetBody
from splitsmith.overlay_hud import HudOptions, OverlayStyleFields, overlay_settings
from splitsmith.ui.export_preview_api import ExportPreviewRequest
from splitsmith.ui.exports_api import ExportStageRequest, MatchExportRequest


class _Body(OverlayStyleFields, BaseModel):
    pass


def test_the_defaults_are_classic_with_every_toggle_on() -> None:
    body = _Body()
    assert body.overlay_variant == "default"
    assert body.hud_options() == HudOptions()


def test_the_fields_become_hud_options() -> None:
    body = _Body(
        overlay_variant="plate",
        overlay_speed_colors=False,
        overlay_class_labels=False,
        overlay_landing=False,
        overlay_position="top-right",
    )
    assert body.hud_options() == HudOptions(
        speed_colors=False, class_labels=False, landing=False, position="top-right"
    )


@pytest.mark.parametrize("bad", [{"overlay_variant": "Not A Name"}, {"overlay_position": "middle"}])
def test_a_malformed_style_is_refused(bad: dict) -> None:
    with pytest.raises(ValidationError):
        _Body(**bad)


@pytest.mark.parametrize("model", [ExportStageRequest, MatchExportRequest, ExportPresetBody])
def test_every_body_that_draws_an_overlay_carries_the_style(model: type[BaseModel]) -> None:
    extra = {"stage_numbers": [1]} if model is MatchExportRequest else {}
    body = model(overlay_variant="pips", overlay_position="bottom-left", **extra)
    assert body.overlay_variant == "pips"  # type: ignore[attr-defined]
    assert body.hud_options().position == "bottom-left"  # type: ignore[attr-defined]


def test_an_export_request_and_a_preview_request_reach_reload_chip_and_stage_bar() -> None:
    """The two new toggles must reach ``HudOptions`` through every body
    that draws an overlay, exactly as ``overlay_landing`` does -- an
    export request (the stage and the match export) and the preview
    request. None of these models re-declares the fields, so there is
    nothing to wire here but a regression pin against one that someday
    does."""
    stage = ExportStageRequest(overlay_reload_chip=True, overlay_stage_bar=True)
    assert stage.hud_options().reload_chip is True
    assert stage.hud_options().stage_bar is True

    match = MatchExportRequest(stage_numbers=[1], overlay_reload_chip=True)
    assert match.hud_options().reload_chip is True
    assert match.hud_options().stage_bar is False

    preview = ExportPreviewRequest(card="overlay", stage_number=1, overlay_stage_bar=True)
    assert preview.hud_options().stage_bar is True
    assert preview.hud_options().reload_chip is False


def test_a_preset_saved_before_the_style_loads_as_classic() -> None:
    body = ExportPresetBody.model_validate({"overlay": True})
    assert body.overlay_variant == "default" and body.hud_options() == HudOptions()


def test_classic_settings_ignore_the_template_options() -> None:
    """Toggling speed colours with Classic chosen must not invalidate a
    Classic overlay on disk: Classic draws none of them."""
    common = {
        "look": "splitsmith",
        "codec": "auto",
        "max_height": None,
        "max_fps": None,
        "audit_revision": "r",
    }
    plain = overlay_settings(variant="default", options=HudOptions(), **common)
    toggled = overlay_settings(variant="default", options=HudOptions(speed_colors=False), **common)
    assert plain == toggled
    styled = overlay_settings(variant="plate", options=HudOptions(), **common)
    assert styled != plain
    assert styled != overlay_settings(variant="plate", options=HudOptions(landing=False), **common)
    assert styled != overlay_settings(variant="plate", options=HudOptions(reload_chip=True), **common)
    assert styled != overlay_settings(variant="plate", options=HudOptions(stage_bar=True), **common)
    assert plain == overlay_settings(variant="default", options=HudOptions(stage_bar=True), **common)


def test_an_unrecorded_overlay_matches_no_request() -> None:
    """Changed on purpose (stage events, Task 1b). An overlay rendered before
    the record existed used to read as the defaults, so an untouched form
    reused it. It carries no audit revision and cannot vouch for the shots
    it shows, so it now matches no request: every such overlay is drawn
    once more."""
    from splitsmith.overlay_hud import LEGACY_OVERLAY_SETTINGS

    assert LEGACY_OVERLAY_SETTINGS["audit_revision"] is None
    for revision in ("none", "0123456789abcdef"):
        assert LEGACY_OVERLAY_SETTINGS != overlay_settings(
            look="splitsmith",
            variant="default",
            options=HudOptions(),
            codec="auto",
            max_height=None,
            max_fps=None,
            audit_revision=revision,
        )


def test_the_record_carries_the_audit_revision_for_every_style() -> None:
    """Classic as well as a template style: a shot edit moves what either
    draws, so the revision sits outside the template-only options gate."""
    common = {"look": "splitsmith", "codec": "auto", "max_height": None, "max_fps": None}
    for variant in ("default", "plate"):
        a = overlay_settings(variant=variant, options=HudOptions(), audit_revision="aaaa", **common)
        b = overlay_settings(variant=variant, options=HudOptions(), audit_revision="bbbb", **common)
        assert a["audit_revision"] == "aaaa"
        assert a != b
