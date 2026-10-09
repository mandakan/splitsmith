// The overlay's fit policy (issue #683 F1): shrink a cell's middle band
// to fit its track, then drop elements by data-drop-priority. Then the
// width: an element whose box runs past the cell (a 52-character
// stage name on a card's slate or lower third) is shrunk on its own and
// then ellipsized; see fitWidth. A cell that fits is left exactly as it
// was. Opt-in, a third step between the two: no grid column's text
// overflows its own column (see fitColumns). Loaded
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
  // Width: an element whose box runs past the cell's safe area (a long
  // name on a card) is shrunk on its own, to no less than WIDTH_FLOOR of
  // its size (a title at the legibility floor reads as a footnote), and
  // what still does not fit keeps its start and ends in an ellipsis. Its
  // neighbours keep their size. The safe area keeps a title off the frame
  // edge; an element inside it is never touched, so a cell that fits draws
  // exactly as it did.
  var WIDTH_FLOOR = 0.6;
  // The text's own extent, not the element's box: a row's elements can
  // be as wide as the row a long sibling stretches, while their text is
  // short. A range ignores clipping, so a nowrap line inside an
  // overflow-hidden value still measures its whole length.
  function textRect(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    return range.getBoundingClientRect();
  }
  // Only text past the cell's real edge is a problem; a line the design
  // set close to the edge (a lower third's inset) stays where it is.
  function pastEdge(el, cell) {
    var r = textRect(el);
    return r.width > 0 && (r.left < cell.left - 0.5 || r.right > cell.right + 0.5);
  }
  // Where an overflowing line must end up: a margin in from each edge it
  // crossed, so a title never touches the frame, and its own start on a
  // side it did not cross.
  function target(r, cell) {
    var margin = Math.max(8, (cell.right - cell.left) * 0.025);
    return {
      left: r.left < cell.left ? cell.left + margin : Math.max(cell.left, Math.min(r.left, cell.left + margin)),
      right: r.right > cell.right ? cell.right - margin : Math.min(cell.right, Math.max(r.right, cell.right - margin)),
    };
  }
  function inside(el, box) {
    var r = textRect(el);
    return r.left >= box.left - 0.5 && r.right <= box.right + 0.5;
  }
  function scaleValues(values, bases, factor) {
    values.forEach(function (value, i) { value.style.fontSize = bases[i] * factor + 'px'; });
  }
  function fitWidth(cell) {
    var edges = cell.getBoundingClientRect();
    if (!(edges.width > 0)) { return; }
    cell.querySelectorAll('.el').forEach(function (el) {
      if (getComputedStyle(el).display === 'none' || !pastEdge(el, edges)) { return; }
      var box = target(textRect(el), edges);
      var values = Array.prototype.slice.call(el.querySelectorAll('.value'));
      var bases = values.map(function (v) { return parseFloat(getComputedStyle(v).fontSize) || 0; });
      var smallest = Math.min.apply(null, bases.filter(function (b) { return b > 0; }).concat([Infinity]));
      var lo = smallest === Infinity ? 1 : Math.min(1, Math.max(WIDTH_FLOOR, window.__splitsmithMinFont / smallest));
      var hi = 1;
      scaleValues(values, bases, lo);
      if (inside(el, box)) {
        for (var i = 0; i < 14; i++) {
          var mid = (lo + hi) / 2;
          scaleValues(values, bases, mid);
          if (inside(el, box)) { lo = mid; } else { hi = mid; }
        }
        scaleValues(values, bases, lo);
        return;
      }
      // At WIDTH_FLOOR and still too long: keep its start, end in "...".
      var r = textRect(el);
      el.style.maxWidth = Math.max(0, box.right - Math.max(r.left, box.left)) + 'px';
      el.style.minWidth = '0';
      values.forEach(function (value) { value.style.textOverflow = 'ellipsis'; });
    });
  }
  // Columns: a figure in a table row (the stage summary's Best / Avg /
  // Worst / Draw, its reload row) is as wide as its own column, not the
  // cell, and its value's overflow: hidden cuts what does not fit: in a
  // portrait card "1.42" drew as "1.4", a plausible wrong figure. So the
  // band shrinks, uniformly as fitHeight does, until every grid column's
  // text fits its column on one line, floored at the legibility floor.
  // Opt-in per document (window.__splitsmithFitColumns, set by the stage
  // summary's HTML only): the live race and the free cell share this
  // file, and their rows change text frame to frame, where a per-frame
  // rescale would make the table jump. A band whose columns fit is left
  // exactly as it was.
  function columnOverflows(stack) {
    var els = stack.querySelectorAll('.group.flow-grid > .el');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (getComputedStyle(el).display === 'none') { continue; }
      var width = el.getBoundingClientRect().width;
      for (var j = 0; j < el.children.length; j++) {
        var child = el.children[j];
        var range = document.createRange();
        range.selectNodeContents(child);
        if (range.getBoundingClientRect().width > width + 0.5) { return true; }
        // A value is nowrap; a caption is plain text, so a caption that
        // wrapped ("Reload / avg") has more than one line box.
        if (child.classList.contains('caption') && range.getClientRects().length > 1) { return true; }
      }
    }
    return false;
  }
  function fitColumns(cell) {
    if (!window.__splitsmithFitColumns) { return; }
    var stack = cell.querySelector('.anchor-middle-center');
    if (!stack || !columnOverflows(stack)) { return; }
    var hi = parseFloat(stack.style.getPropertyValue('--fit-scale')) || 1;
    var lo = hi * floorFactor(stack);
    stack.style.setProperty('--fit-scale', String(lo));
    if (columnOverflows(stack)) { return; }
    for (var i = 0; i < 14; i++) {
      var mid = (lo + hi) / 2;
      stack.style.setProperty('--fit-scale', String(mid));
      if (columnOverflows(stack)) { hi = mid; } else { lo = mid; }
    }
    stack.style.setProperty('--fit-scale', String(lo));
  }
  document.querySelectorAll('.cell').forEach(function (cell) {
    fitHeight(cell);
    fitColumns(cell);
    fitWidth(cell);
  });
  function fitHeight(cell) {
    var stack = cell.querySelector('.anchor-middle-center');
    if (!stack) { return; }
    var available = availableHeight(cell);
    if (!(available > 0)) { return; }
    if (fits(stack, available)) { return; }
    shrinkToFit(stack, available);
    if (fits(stack, available)) { return; }
    dropUntilFit(stack, available);
  }
};
