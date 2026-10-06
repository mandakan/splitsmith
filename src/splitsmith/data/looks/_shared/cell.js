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
  engine.mount = function (groups) { document.body.innerHTML = cellHtml(groups); };
  if (typeof window.duration !== 'function') { window.duration = function () { return 0; }; }
  if (typeof window.seek !== 'function') { window.seek = function () {}; }
})();
