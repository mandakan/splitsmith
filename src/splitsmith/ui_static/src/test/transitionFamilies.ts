/**
 * A slice of the server's xfade families (``GET /api/looks`` ``transitions``,
 * #1259) for the gallery, mapper and page tests: a single-kind family and
 * three with directions.
 */
import type { TransitionFamilyInfo } from "@/lib/api";

export const FAMILIES: TransitionFamilyInfo[] = [
  { id: "fade", label: "Fade", help: "Fades the stage into the next.", preview: "/api/looks/_transitions/preview/fade.webp", directions: [{ name: "default", kind: "fade" }] },
  {
    id: "slide",
    label: "Slide",
    help: "The next stage slides in, pushing this one out.",
    preview: "/api/looks/_transitions/preview/slide.webp",
    directions: [
      { name: "left", kind: "slideleft" },
      { name: "right", kind: "slideright" },
      { name: "up", kind: "slideup" },
      { name: "down", kind: "slidedown" },
    ],
  },
  {
    id: "wind",
    label: "Wind",
    help: "This stage blows away in streaks.",
    preview: "/api/looks/_transitions/preview/wind.webp",
    directions: [
      { name: "left", kind: "hlwind" },
      { name: "right", kind: "hrwind" },
      { name: "up", kind: "vuwind" },
      { name: "down", kind: "vdwind" },
    ],
  },
  {
    id: "circle",
    label: "Circle",
    help: "A circle opens onto the next stage, or closes on this one.",
    preview: "/api/looks/_transitions/preview/circle.webp",
    directions: [
      { name: "open", kind: "circleopen" },
      { name: "close", kind: "circleclose" },
    ],
  },
];

export const FAMILY_KINDS: string[] = FAMILIES.flatMap((f) => f.directions.map((d) => d.kind));
