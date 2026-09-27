/**
 * Bring a row into view inside its own scroll container, and nowhere else.
 *
 * ``Element.scrollIntoView`` scrolls every scrollable ancestor, the
 * document included: the Audit shot list used it to follow the current
 * shot, so placing or toggling a marker jumped the whole page (#1067).
 * This moves only ``container.scrollTop``, with ``block: "nearest"``
 * semantics: no change when the row is already fully visible, else the
 * smallest move that shows it.
 */
export function nearestScrollTop(
  scrollTop: number,
  viewHeight: number,
  rowTop: number,
  rowHeight: number,
): number {
  const rowBottom = rowTop + rowHeight;
  if (rowTop < scrollTop) return rowTop;
  if (rowBottom > scrollTop + viewHeight) {
    // A row taller than the view aligns its top, like the browser does.
    return rowHeight > viewHeight ? rowTop : rowBottom - viewHeight;
  }
  return scrollTop;
}

export function scrollRowIntoContainer(container: HTMLElement, row: HTMLElement): void {
  const box = container.getBoundingClientRect();
  const r = row.getBoundingClientRect();
  // Row top in the container's content coordinates.
  const rowTop = container.scrollTop + (r.top - box.top) - container.clientTop;
  const next = nearestScrollTop(container.scrollTop, container.clientHeight, rowTop, r.height);
  if (next !== container.scrollTop) container.scrollTop = next;
}
