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
    if (window.__splitsmithFitUniform) {
      // An upright grid pads a tile out of the platform's button column:
      // its text must end inside that padding, not at the tile's edge.
      var inset = parseFloat(getComputedStyle(cell).paddingRight) || 0;
      edges = {left: edges.left, right: edges.right - inset, top: edges.top, bottom: edges.bottom};
    }
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
  // Text that exactly fills its column runs into the next one: "Reload
  // avg" and "Exposed" read as one phrase. So a column's text also
  // overflows when it ends closer than COLUMN_GAP_EM to the start of the
  // next column in the same row; the last column of a row has nothing to
  // run into. The em is the band's caption size for every column, a
  // caption-less row label ("Static") included: the caption is the
  // smaller text, the one that collides, so one base reads the same gap
  // everywhere. "Same row" is vertical overlap, not equal tops: a table
  // row sits on its last baseline, so a caption-less label sits lower
  // than the captioned figures beside it.
  var COLUMN_GAP_EM = 0.6;
  function nextInRow(el) {
    var next = el.nextElementSibling;
    while (next && getComputedStyle(next).display === 'none') { next = next.nextElementSibling; }
    if (!next) { return null; }
    var a = el.getBoundingClientRect();
    var b = next.getBoundingClientRect();
    return a.top < b.bottom && b.top < a.bottom && b.left > a.left ? b : null;
  }
  // ``figuresOnly`` asks the narrower question the fallback below needs:
  // does any text run past its column or into the next one? A caption
  // that wrapped onto a second line is legible and does not count.
  function columnOverflows(stack, figuresOnly) {
    var els = stack.querySelectorAll('.group.flow-grid > .el');
    var anyCaption = stack.querySelector('.caption');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (getComputedStyle(el).display === 'none') { continue; }
      var width = el.getBoundingClientRect().width;
      var next = nextInRow(el);
      for (var j = 0; j < el.children.length; j++) {
        var child = el.children[j];
        var range = document.createRange();
        range.selectNodeContents(child);
        var rect = range.getBoundingClientRect();
        if (rect.width > width + 0.5) { return true; }
        if (next) {
          var em = parseFloat(getComputedStyle(anyCaption || child).fontSize);
          if (rect.right > next.left - COLUMN_GAP_EM * em + 0.5) { return true; }
        }
        // A value is nowrap; a caption is plain text, so a caption that
        // wrapped ("Reload / avg") has more than one line box.
        if (!figuresOnly && child.classList.contains('caption') && range.getClientRects().length > 1) {
          return true;
        }
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
    if (columnOverflows(stack)) {
      fitColumnsPastFloor(cell, stack, hi);
      return;
    }
    for (var i = 0; i < 14; i++) {
      var mid = (lo + hi) / 2;
      stack.style.setProperty('--fit-scale', String(mid));
      if (columnOverflows(stack)) { hi = mid; } else { lo = mid; }
    }
    stack.style.setProperty('--fit-scale', String(lo));
  }
  // At the floor and a figure still runs into the next column: a dense
  // upright grid hold, whose quarter columns are narrower than "0.30" at
  // the size its captions reach the floor. The captions and the band's
  // labels set the floor, so the uniform shrink above cannot make the
  // figures any smaller. In order, the first layout that fits wins:
  //   1. the band wraps: each table row takes half its columns per line
  //      (Best / Avg over Worst / Draw) and keeps every caption;
  //   2. one line again, without the row's captions;
  //   3. wrapped and without captions.
  // In each the band's text may shrink, every size by one factor but none
  // below the floor, so the figures keep their rank against hit factor
  // and time while the labels already at the floor stay. The band keeps
  // the height it fitted in. A band whose figures fit at the floor (a
  // caption that wrapped, as on a dense match summary) is left exactly as
  // it was.
  function fitColumnsPastFloor(cell, stack, hi) {
    if (!columnOverflows(stack, true)) { return; }
    // Marks the band for tests: no style reads it.
    stack.setAttribute('data-fit-columns', 'past-floor');
    var available = availableHeight(cell);
    var heightFit = !(available > 0) || fits(stack, available);
    var groups = Array.prototype.slice.call(stack.querySelectorAll('.group.flow-grid'));
    var captions = Array.prototype.slice.call(stack.querySelectorAll('.group.flow-grid > .el > .caption'));
    var texts = Array.prototype.slice.call(stack.querySelectorAll('.value, .caption'));
    var columns = groups.map(function (group) {
      return getComputedStyle(group).gridTemplateColumns.split(' ').length;
    });
    var floorPx = window.__splitsmithMinFont;
    function ok() {
      return !columnOverflows(stack, true) && (!heightFit || fits(stack, available));
    }
    // The largest factor in [floor, 1] at which ok() holds, applied;
    // false, left at the floor, when none does.
    function shrinkText() {
      var bases = texts.map(function (text) { return parseFloat(getComputedStyle(text).fontSize) || 0; });
      var largest = Math.max.apply(null, bases.concat([0]));
      function apply(factor) {
        texts.forEach(function (text, i) {
          var base = bases[i];
          text.style.fontSize = (base <= floorPx ? base : Math.max(floorPx, base * factor)) + 'px';
        });
      }
      apply(1);
      if (ok()) { return true; }
      var lo = largest > floorPx ? floorPx / largest : 1;
      var top = 1;
      apply(lo);
      if (!ok()) { return false; }
      for (var i = 0; i < 14; i++) {
        var mid = (lo + top) / 2;
        apply(mid);
        if (ok()) { lo = mid; } else { top = mid; }
      }
      apply(lo);
      return true;
    }
    function layout(wrap, captionless) {
      stack.style.setProperty('--fit-scale', String(hi));
      texts.forEach(function (text) { text.style.fontSize = ''; });
      captions.forEach(function (caption) { caption.style.display = captionless ? 'none' : ''; });
      groups.forEach(function (group, i) {
        group.style.gridTemplateColumns = wrap && columns[i] > 2 ? 'repeat(' + Math.ceil(columns[i] / 2) + ', 1fr)' : '';
      });
      return shrinkText();
    }
    if (layout(true, false)) { return; }
    if (layout(false, true)) { return; }
    layout(true, true);
  }
  // Uniform (opt-in, window.__splitsmithFitUniform, an upright compare
  // grid only): every tile ends at the smallest scale any tile's band
  // needed, so a tile with less to say (no splits) never draws its figures
  // larger than its neighbours'. A band that took the past-floor layouts
  // sets its own sizes and is left as it is. Cells are fixed grid tracks,
  // so fitting one never moves another and the width step can run after.
  var cells = Array.prototype.slice.call(document.querySelectorAll('.cell'));
  cells.forEach(function (cell) {
    fitHeight(cell);
    fitColumns(cell);
  });
  if (window.__splitsmithFitUniform) { fitUniform(cells); }
  cells.forEach(fitWidth);
  function fitUniform(all) {
    var stacks = all.map(function (cell) { return cell.querySelector('.anchor-middle-center'); })
      .filter(function (stack) { return stack && !stack.hasAttribute('data-fit-columns'); });
    var scales = stacks.map(function (stack) { return parseFloat(stack.style.getPropertyValue('--fit-scale')) || 1; });
    var least = Math.min.apply(null, scales.concat([1]));
    stacks.forEach(function (stack, i) {
      if (scales[i] > least) { stack.style.setProperty('--fit-scale', String(least)); }
    });
  }
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
