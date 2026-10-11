// The cell markup builder: a port of overlay_html._cell_div and the
// helpers it calls, so a Look template can render a declared cell with
// the engine's own stylesheet (window.splitsmith.engine.css). Keep the
// two in step: tests/test_look_template.py compares the DOM this builds
// with the DOM Python builds for the same groups.
(function () {
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;');
  }
  function isBottom(anchor) { return anchor === 'bottom-left' || anchor === 'bottom-center' || anchor === 'bottom-right'; }
  function isRight(anchor) { return anchor === 'top-right' || anchor === 'bottom-right'; }
  function isCenter(anchor) { return anchor === 'top-center' || anchor === 'middle-center' || anchor === 'bottom-center'; }
  function fit(px) { return 'calc(var(--fit-scale, 1) * ' + px + 'px)'; }

  function anchorClasses(anchor, align) {
    var classes = ['anchor', 'anchor-' + anchor, isBottom(anchor) ? 'stack-reverse' : 'stack-normal'];
    var resolved = align;
    if (resolved == null) {
      resolved = isRight(anchor) ? 'right' : isCenter(anchor) ? 'center' : 'left';
    }
    classes.push('align-' + resolved);
    return classes.join(' ');
  }
  function groupClasses(g) {
    var classes = ['group', 'flow-' + g.flow];
    if (g.flow === 'column') {
      classes.push(isRight(g.anchor) ? 'align-right' : isCenter(g.anchor) ? 'align-center' : 'align-left');
    }
    return classes.join(' ');
  }
  function colorClass(color) { return color == null ? '' : 'tok-' + color.replace(/_/g, '-'); }
  function elementDiv(e) {
    var elClasses = e.role === 'identity' ? 'el el-identity' : 'el';
    var caption = e.caption != null ? '<span class="caption">' + esc(e.caption) + '</span>' : '';
    var unit = e.unit != null ? '<span class="unit">' + esc(e.unit) + '</span>' : '';
    var valueClasses = ['value', 'role-' + e.role, 'emphasis-' + e.emphasis, colorClass(e.color)]
      .filter(function (c) { return c; }).join(' ');
    var value = '<span class="' + valueClasses + '">' + esc(e.text) + unit + '</span>';
    var priority = e.drop_priority != null ? ' data-drop-priority="' + e.drop_priority + '"' : '';
    return '<div class="' + elClasses + '"' + priority + '>' + caption + value + '</div>';
  }
  function groupStyle(g) {
    var parts = [];
    if (g.flow === 'grid') { parts.push('grid-template-columns: repeat(' + Math.max(1, g.elements.length) + ', 1fr)'); }
    if (g.gap != null) { parts.push('gap: ' + fit(g.gap)); }
    if (g.margin_top != null) { parts.push('margin-top: ' + fit(g.margin_top)); }
    return parts.length ? ' style="' + parts.join('; ') + '"' : '';
  }
  function groupDiv(g) {
    if (g.divider) { return '<div class="group divider"></div>'; }
    return '<div class="' + groupClasses(g) + '"' + groupStyle(g) + '>' + g.elements.map(elementDiv).join('') + '</div>';
  }
  function anchorDiv(anchor, members) {
    var align = null;
    for (var i = 0; i < members.length; i++) {
      if (members[i].align != null) { align = members[i].align; break; }
    }
    return '<div class="' + anchorClasses(anchor, align) + '">' + members.map(groupDiv).join('') + '</div>';
  }
  function cellHtml(groups) {
    var order = [];
    var buckets = {};
    groups.forEach(function (g) {
      if (!buckets[g.anchor]) { buckets[g.anchor] = []; order.push(g.anchor); }
      buckets[g.anchor].push(g);
    });
    return '<div class="cell">' + order.map(function (a) { return anchorDiv(a, buckets[a]); }).join('') + '</div>';
  }

  var engine = window.splitsmith.engine;
  engine.renderGroups = cellHtml;
  engine.mount = function (groups) {
    document.body.innerHTML = cellHtml(groups);
    // An upright card's name may take two lines rather than end in "..."
    // (fit.js fitLines, #1431); a name that fits one line is untouched.
    var size = window.splitsmith.size;
    if (size && size.height > size.width) { window.__splitsmithFitLines = 2; }
    // The platform safe area of an upright card (the engine stylesheet's
    // --safe-bottom / --safe-right, safe_area.py): the cell keeps its text
    // above the bottom band and in from the button column, on both sides
    // so a centred line stays centred; fit.js measures to that padding.
    var root = getComputedStyle(document.documentElement);
    var bottom = parseFloat(root.getPropertyValue('--safe-bottom')) || 0;
    var right = parseFloat(root.getPropertyValue('--safe-right')) || 0;
    var cell = document.body.firstElementChild;
    if ((bottom > 0 || right > 0) && cell) {
      cell.style.padding = '0 ' + right + 'px ' + bottom + 'px';
      window.__splitsmithFitInset = true;
      // A corner anchor (a lower third) is placed against the cell's
      // padding box, which the padding does not move: lift it too.
      cell.querySelectorAll('.anchor-bottom-left, .anchor-bottom-right').forEach(function (a) {
        a.style.bottom = parseFloat(getComputedStyle(a).bottom) + bottom + 'px';
      });
      cell.querySelectorAll('.anchor-bottom-right').forEach(function (a) {
        a.style.right = parseFloat(getComputedStyle(a).right) + right + 'px';
      });
    }
  };
  if (typeof window.duration !== 'function') { window.duration = function () { return 0; }; }
  if (typeof window.seek !== 'function') { window.seek = function () {}; }
})();
