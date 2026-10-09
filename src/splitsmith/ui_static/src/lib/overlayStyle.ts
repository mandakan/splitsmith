/**
 * The overlay style (template HUD, spec 2026-10-08): which of the chosen
 * Look's HUD templates draws the live overlay, its three toggles and its
 * position. ``default`` is Classic, the engine's own overlay. Pure: the
 * catalog (``GET /api/looks``) says which styles a Look has and where
 * each can sit; this module resolves a stored style against it and says
 * what a request carries. A request carries the fields only when a style
 * is chosen, so an untouched form sends the body it always sent.
 */
import type { LookInfo, LookVariantInfo, OverlayStyleBody } from "@/lib/api";
import { DEFAULT_VARIANT, visibleLook } from "@/lib/looks";

export interface OverlayStyle {
  /** The Look's ``overlay`` variant; ``default`` is Classic. */
  variant: string;
  speedColors: boolean;
  classLabels: boolean;
  landing: boolean;
  /** ``null`` is the style's own default position. */
  position: string | null;
}

export const DEFAULT_OVERLAY_STYLE: OverlayStyle = {
  variant: DEFAULT_VARIANT,
  speedColors: false,
  classLabels: true,
  landing: true,
  position: null,
};

/** The chosen Look's overlay styles, as the catalog lists them. */
export function overlayStylesFor(looks: LookInfo[], look: string): LookVariantInfo[] {
  const name = visibleLook(looks, look);
  return looks.find((l) => l.name === name)?.slots.overlay ?? [];
}

/** The positions a style declares, its default first; empty for Classic
 *  and for a style that places itself. */
export function overlayStylePositions(looks: LookInfo[], look: string, variant: string): string[] {
  return overlayStylesFor(looks, look).find((v) => v.name === variant)?.positions ?? [];
}

/** The stored style as the installed catalog can draw it: a style the Look
 *  lacks is Classic and a position the style does not declare is its
 *  default; the toggles are kept, so they come back with the style. */
export function visibleOverlayStyle(looks: LookInfo[], look: string, style: OverlayStyle): OverlayStyle {
  if (style.variant === DEFAULT_VARIANT) return style;
  const styles = overlayStylesFor(looks, look);
  const hit = styles.find((v) => v.name === style.variant);
  if (!hit) return { ...style, variant: DEFAULT_VARIANT };
  if (style.position !== null && !(hit.positions ?? []).includes(style.position)) return { ...style, position: null };
  return style;
}

/** What a request carries: nothing for Classic, else the style, its
 *  toggles and a chosen position. */
export function overlayStyleFields(style: OverlayStyle): OverlayStyleBody {
  if (style.variant === DEFAULT_VARIANT) return {};
  return {
    overlay_variant: style.variant,
    overlay_speed_colors: style.speedColors,
    overlay_class_labels: style.classLabels,
    overlay_landing: style.landing,
    ...(style.position !== null ? { overlay_position: style.position } : {}),
  };
}

/** The preset body's fields: always all five, so a preset round-trips. */
export function overlayStyleBody(style: OverlayStyle): Required<OverlayStyleBody> {
  return {
    overlay_variant: style.variant,
    overlay_speed_colors: style.speedColors,
    overlay_class_labels: style.classLabels,
    overlay_landing: style.landing,
    overlay_position: style.position,
  };
}

/** A preset body's style; a body saved before styles existed is Classic. */
export function styleFromBody(body: OverlayStyleBody): OverlayStyle {
  const D = DEFAULT_OVERLAY_STYLE;
  return {
    variant: body.overlay_variant ?? D.variant,
    speedColors: body.overlay_speed_colors ?? D.speedColors,
    classLabels: body.overlay_class_labels ?? D.classLabels,
    landing: body.overlay_landing ?? D.landing,
    position: body.overlay_position ?? D.position,
  };
}

/** What the page calls a style: "Classic" for the engine's own. */
export function overlayStyleLabel(variant: string): string {
  if (variant === DEFAULT_VARIANT) return "Classic";
  return variant.charAt(0).toUpperCase() + variant.slice(1).replace(/[-_]/g, " ");
}
