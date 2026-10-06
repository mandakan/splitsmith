// The overlay's fit policy (issue #683 F1): shrink a cell's middle band
// to fit its track, then drop elements by data-drop-priority. Loaded
// inline by overlay_html._fit_script() and by file URL from a Look's
// card template. The legibility floor comes from
// window.__splitsmithMinFont, which the caller sets before calling
// window.__splitsmithFit(); the file bakes in no number.
window.__splitsmithFit = function () {
  function fits(stack, available) {
    return stack.scrollHeight <= available + 0.5;
  }
  function availableHeight(cell) {
    var rows = getComputedStyle(cell).gridTemplateRows.split(' ').map(parseFloat);
    return rows.length > 1 ? rows[1] : cell.clientHeight;
  }
  function floorFactor(stack) {
    var min = Infinity;
    stack.querySelectorAll('.value, .caption, .unit').forEach(function (el) {
      var size = parseFloat(getComputedStyle(el).fontSize);
      if (size > 0 && size < min) { min = size; }
    });
    return min === Infinity ? 1 : Math.min(1, window.__splitsmithMinFont / min);
  }
  function shrinkToFit(stack, available) {
    var lo = floorFactor(stack);
    var hi = 1;
    stack.style.setProperty('--fit-scale', String(hi));
    if (fits(stack, available)) { return; }
    stack.style.setProperty('--fit-scale', String(lo));
    if (!fits(stack, available)) { return; }
    for (var i = 0; i < 14; i++) {
      var mid = (lo + hi) / 2;
      stack.style.setProperty('--fit-scale', String(mid));
      if (fits(stack, available)) { lo = mid; } else { hi = mid; }
    }
    stack.style.setProperty('--fit-scale', String(lo));
  }
  function normalizeLeadingMargin(stack) {
    // ``Group.margin_top`` (see ``overlay_summary._cell_groups``' own
    // "Splits" label group) is baked into the HTML at Python time,
    // before this script ever runs, to separate the Splits band from a
    // Scoring band that Python believed would be above it. Collapsing an
    // emptied Scoring group (below) removes its own gap but leaves that
    // margin behind on whatever group is now the flex column's first
    // VISIBLE child -- space meant to separate two bands from each
    // other, now separating one band from nothing. Re-zeroing it on
    // whichever group ends up first-visible, every time the set of
    // hidden groups changes, is what a real box model gives for free
    // when there is nothing above to separate from; the browser cannot
    // do that itself because the margin is this group's own property,
    // not the (already ``display: none``, already zero-height) group
    // before it.
    var seenVisible = false;
    Array.prototype.forEach.call(stack.children, function (child) {
      if (getComputedStyle(child).display === 'none') { return; }
      if (!seenVisible) {
        seenVisible = true;
        if (child.style.marginTop) { child.style.marginTop = '0px'; }
      }
    });
  }
  function dropUntilFit(stack, available) {
    var candidates = Array.prototype.slice.call(stack.querySelectorAll('[data-drop-priority]'));
    candidates.sort(function (a, b) {
      var ap = parseInt(a.getAttribute('data-drop-priority'), 10);
      var bp = parseInt(b.getAttribute('data-drop-priority'), 10);
      return ap - bp;
    });
    for (var i = 0; i < candidates.length; i++) {
      if (fits(stack, available)) { return; }
      var el = candidates[i];
      el.style.display = 'none';
      // A ``.group`` with every child now hidden still sits in the
      // ``.anchor-middle-center`` flex column and still consumes a
      // ``row_gutter`` gap -- an emptied group is not a zero-height one.
      // Left alone, those leftover gaps are exactly what pushed the
      // Splits band's own values out of the cell even after this loop
      // had correctly stopped dropping them: see the fix-round report
      // for the measured 58px residual this closes. Hiding the group
      // itself once nothing inside it is visible removes its gap from
      // the flex column entirely, the same way ``display: none`` already
      // removes each dropped ``.el``'s own space.
      var group = el.closest('.group');
      if (group) {
        var allHidden = Array.prototype.every.call(group.children, function (child) {
          return getComputedStyle(child).display === 'none';
        });
        if (allHidden) {
          group.style.display = 'none';
          normalizeLeadingMargin(stack);
        }
      }
    }
  }
  document.querySelectorAll('.cell').forEach(function (cell) {
    var stack = cell.querySelector('.anchor-middle-center');
    if (!stack) { return; }
    var available = availableHeight(cell);
    if (!(available > 0)) { return; }
    if (fits(stack, available)) { return; }
    shrinkToFit(stack, available);
    if (fits(stack, available)) { return; }
    dropUntilFit(stack, available);
  });
};
