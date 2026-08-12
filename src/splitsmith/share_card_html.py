"""Card model to a 1200x630 HTML document (spec 2026-08-09).

**This module is pure.** No browser, no Playwright import, no file I/O
beyond resolving a path string for ``@font-face``. Rasterizing what it
returns is ``overlay_raster.py``'s job.

Two constraints carry over verbatim from ``overlay_html.py``, both
load-bearing:

- The ``@font-face`` ``src`` is a ``file://`` URL naming a bundled TTF,
  and the rasterizer must NAVIGATE to a written document rather than
  calling ``page.set_content()``. That module's docstring records the
  measurement: under ``set_content`` the custom face silently fails to
  load and Chromium substitutes a host font, with no error and no
  exception.
- Every box sets ``overflow: hidden`` and long strings clamp in CSS.
  Nothing here measures text or decides a size in Python -- that
  arithmetic is exactly what kept reappearing as a defect in the
  pre-browser fitter.

A competitor's name is untrusted input, so every interpolated string
goes through :func:`html.escape`.
"""

from __future__ import annotations

from html import escape

from .overlay_html import FONT_FILES, font_face_url
from .overlay_theme import RGB, OverlayTheme
from .share_card import CompareCard, MatchCard, StageCard

CARD_WIDTH = 1200
CARD_HEIGHT = 630


def _rgb(value: RGB) -> str:
    r, g, b = value
    return f"#{r:02x}{g:02x}{b:02x}"


_BRAND_MARK = (
    '<svg viewBox="0 0 36 36" width="36" height="36" fill="none" aria-hidden="true">'
    '<rect x="1.5" y="1.5" width="33" height="33" rx="7" fill="{surface}" stroke="{rule}" stroke-width="1"/>'
    '<rect x="10" y="8" width="3" height="20" rx="1.2" fill="{ink}"/>'
    '<rect x="23" y="8" width="3" height="20" rx="1.2" fill="{ink}"/>'
    '<circle cx="18" cy="18" r="2.4" fill="{accent}"/>'
    "</svg>"
)


def _style(theme: OverlayTheme) -> str:
    display_url = font_face_url(FONT_FILES["display"])
    mono_url = font_face_url(FONT_FILES["mono"])
    return f"""<style>
@font-face {{ font-family: "Antonio"; src: url("{display_url}") format("truetype");
             font-weight: 400 700; font-display: block; }}
@font-face {{ font-family: "JetBrains Mono"; src: url("{mono_url}") format("truetype");
             font-weight: 700; font-display: block; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
.card {{ width: {CARD_WIDTH}px; height: {CARD_HEIGHT}px; overflow: hidden;
         display: flex; flex-direction: column; padding: 56px 72px;
         background: linear-gradient(to bottom, {_rgb(theme.surface)}, {_rgb(theme.stroke)});
         color: {_rgb(theme.ink)}; font-family: sans-serif; }}
.top {{ display: flex; align-items: center; justify-content: space-between; overflow: hidden; }}
.brand {{ display: flex; align-items: center; gap: 14px; overflow: hidden; }}
.wordmark {{ font-family: "Antonio"; font-weight: 700; font-size: 28px; line-height: 0.9;
             text-transform: uppercase; letter-spacing: -0.02em; }}
.kick {{ font-family: "JetBrains Mono"; font-weight: 700; font-size: 15px; letter-spacing: 0.2em;
         text-transform: uppercase; color: {_rgb(theme.subtle)}; overflow: hidden;
         white-space: nowrap; text-overflow: ellipsis; }}
.hot {{ color: {_rgb(theme.accent)}; }}
.body {{ flex: 1; display: flex; align-items: center; gap: 56px; overflow: hidden; }}
.display {{ font-family: "Antonio"; font-weight: 700; text-transform: uppercase;
            line-height: 1.08; letter-spacing: -0.01em; overflow: hidden;
            padding-bottom: 0.12em; display: -webkit-box; -webkit-line-clamp: 2;
            -webkit-box-orient: vertical; }}
.num {{ font-family: "JetBrains Mono"; font-weight: 700; font-variant-numeric: tabular-nums;
        letter-spacing: -0.03em; line-height: 0.86; }}
.vrule {{ width: 1px; align-self: stretch; margin: 14px 0; background: {_rgb(theme.rule)}; }}
.hrule {{ height: 1px; background: {_rgb(theme.rule)}; margin-bottom: 22px; }}
.stagebody {{ flex: 1; display: grid; grid-template-columns: minmax(0, 500px) 1px minmax(420px, 1fr);
              align-items: center; gap: 48px; overflow: hidden; }}
.figs {{ display: flex; gap: 48px; overflow: hidden; }}
.fig {{ display: flex; flex-direction: column; gap: 8px; overflow: hidden; }}
.fig .v {{ font-size: 88px; }}
.col {{ display: flex; flex-direction: column; gap: 14px; flex: 1; overflow: hidden; }}
.roster {{ display: flex; flex-direction: column; gap: 14px; width: 430px; overflow: hidden; }}
.rrow {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
         overflow: hidden; }}
.badge {{ display:inline-block; padding: 6px 14px; border: 2px solid {_rgb(theme.accent)};
          color: {_rgb(theme.accent)}; font-family: "JetBrains Mono"; font-size: 28px;
          letter-spacing: 0.08em; border-radius: 6px; }}
</style>"""


def _brand_row(theme: OverlayTheme) -> str:
    """The mark + wordmark, built once at construction time so the mark
    never has to be spliced into an already-built document.

    ``_document`` used to carry a ``{MARK}`` placeholder through
    already-escaped user text and replace it in a second pass. That is
    the wrong shape: ``html.escape`` does not touch ``{`` or ``}``, so a
    user field containing the literal substring ``{MARK}`` collided with
    the placeholder and had its own text silently swapped for the brand
    SVG. Building the mark inline, before any user text joins the
    string, removes the second pass -- and the collision -- entirely.
    """
    mark = _BRAND_MARK.format(
        surface=_rgb(theme.surface),
        rule=_rgb(theme.rule),
        ink=_rgb(theme.ink),
        accent=_rgb(theme.accent),
    )
    return f'<div class="brand">{mark}<div class="wordmark">Splitsmith</div></div>'


def _document(theme: OverlayTheme, body: str) -> str:
    """Wrap an already-finished body in the document shell. Performs no
    substitution of its own -- ``body`` is final by the time it gets
    here; see :func:`_brand_row`."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{_style(theme)}</head><body>"
        f'<div class="card">{body}</div>'
        "</body></html>"
    )


#: Colours come from the stylesheet, so this is a constant, not a function.
_FOOTER = (
    '<div class="hrule"></div>'
    '<div class="kick">Per-shot splits from stage video '
    '<span class="hot">&middot;</span> splitsmith.app</div>'
)


def _moment_badge(moment_t: float | None) -> str:
    """A ``MOMENT {t:.2f}s`` strip for a moment-linked card, or nothing.

    ``moment_t`` is already-safe -- it is a float straight off the
    request, never user markup -- so this is the one interpolation in
    the module that skips :func:`escape`.
    """
    if moment_t is None:
        return ""
    return f'<div class="badge">MOMENT {moment_t:.2f}s</div>'


def match_card_html(card: MatchCard, *, theme: OverlayTheme) -> str:
    """Identity plus roster. Carries no aggregate time by design."""
    meta = [f"{card.stage_count} stages"]
    if card.match_date:
        meta.append(escape(card.match_date))
    rows = "".join(
        f'<div class="rrow"><div class="display" style="font-size:34px">{escape(r.name)}</div>'
        f'<div class="kick">{escape(r.division or "")}</div></div>'
        for r in card.roster
    )
    label = f"{len(card.roster)} shooters" if len(card.roster) != 1 else "Shooter"
    body = (
        f'<div class="top">{_brand_row(theme)}'
        f'<div class="kick">{" &middot; ".join(meta)}</div></div>'
        '<div class="body">'
        f'<div class="col"><div class="display" style="font-size:96px">'
        f"{escape(card.match_name)}</div></div>"
        '<div class="vrule"></div>'
        f'<div class="roster"><div class="kick">{escape(label)}</div>{rows}</div>'
        "</div>" + _FOOTER
    )
    return _document(theme, body)


def stage_card_html(card: StageCard, *, theme: OverlayTheme) -> str:
    """Draw and average split, the two numbers splitsmith computes.

    The average numeral is dropped entirely when there is nothing to
    average -- the figures sit in a flex row, so removing one closes the
    layout up. Never a zero or a dash standing in for a real figure.
    """
    figs = []
    if card.figures.draw is not None:
        figs.append(_figure(f"{card.figures.draw:.2f}", "Draw"))
    if card.figures.avg_split is not None:
        caption = f"Avg split &middot; {card.figures.split_count} of {card.figures.interval_count}"
        figs.append(_figure(f"{card.figures.avg_split:.3f}", caption))

    meta = [f"Stage {card.stage_number}", f"{card.shot_count} shots"]
    if card.stage_time is not None:
        meta.append(f"{card.stage_time:.2f}s")

    body = (
        f'<div class="top">{_brand_row(theme)}'
        f'<div class="kick">{" &middot; ".join(escape(m) for m in meta)}</div></div>'
        f"{_moment_badge(card.moment_t)}"
        f'<div class="stagebody"><div class="figs">{"".join(figs)}</div>'
        '<div class="vrule"></div>'
        f'<div class="col"><div class="display" style="font-size:44px">'
        f"{escape(card.stage_name)}</div>"
        f'<div class="kick">{escape(card.shooter_name)}</div>'
        f'<div class="kick">{escape(card.match_name)}</div></div>'
        "</div>" + _FOOTER
    )
    return _document(theme, body)


def compare_card_html(card: CompareCard, *, theme: OverlayTheme) -> str:
    """Who is being compared, on which stage. Mirrors ``match_card_html``'s
    headline: the compared shooters' names, joined " vs ", carry the
    96px ``.display`` slot -- the card's most identifying string --
    rather than a static "Compare" label. The meta line still carries
    the stage and match identity; there is no separate roster listing
    because it would just repeat the same names a second time.
    """
    meta = [f"Stage {card.stage_number} - {escape(card.stage_name)}", escape(card.match_name)]
    headline = " vs ".join(escape(name) for name in card.shooter_names)
    body = (
        f'<div class="top">{_brand_row(theme)}'
        f'<div class="kick">{" &middot; ".join(meta)}</div></div>'
        f"{_moment_badge(card.moment_t)}"
        '<div class="body">'
        f'<div class="col"><div class="display" style="font-size:96px">'
        f"{headline}</div></div>"
        "</div>" + _FOOTER
    )
    return _document(theme, body)


def _figure(value: str, caption: str) -> str:
    """One numeral block, rendered in a single weight and colour.

    An earlier version dimmed the fractional part (``.32`` in a lighter
    grey than the leading ``1``). At the 88px this renders at, that made
    the bright leading digit alone read as the headline number -- "1" or
    "0" -- with the rest receding, exactly the opposite of what a
    glanceable card needs. ``caption`` is already-escaped markup.
    """
    return f'<div class="fig"><div class="num v">{escape(value)}</div><div class="kick">{caption}</div></div>'
