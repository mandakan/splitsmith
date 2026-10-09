/** Scroll-host geometry handed to every timeline track. */
export interface TimelineGeom {
  /** Zoomed content width, CSS px (the viewport at Fit). */
  contentWidth: number;
  /** Visible width of the scroll host, CSS px. */
  viewportWidth: number;
  scrollLeft: number;
  pxPerSec: number;
}
