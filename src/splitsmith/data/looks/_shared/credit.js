// "Made with splitsmith" on a card: the app's crosshair mark and a small
// line, centred at the bottom of the closing card. On by default, turned
// off under Details on the Export page. ``data.credit`` is only there when
// it is on, on the closing card, so every other card renders the same
// bytes as before this file existed. Drawn inline: no file to mount.
(function () {
  var engine = window.splitsmith.engine;
  var SVG = 'http://www.w3.org/2000/svg';
  function mark(px, colour) {
    var svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('viewBox', '0 0 32 32');
    svg.setAttribute('width', px);
    svg.setAttribute('height', px);
    var parts = [
      ['circle', { cx: 16, cy: 16, r: 13, fill: 'none', stroke: colour, 'stroke-width': 1.5 }],
      ['circle', { cx: 16, cy: 16, r: 3.5, fill: colour }],
      ['line', { x1: 16, y1: 1, x2: 16, y2: 6, stroke: colour, 'stroke-width': 1.5 }],
      ['line', { x1: 16, y1: 26, x2: 16, y2: 31, stroke: colour, 'stroke-width': 1.5 }],
      ['line', { x1: 1, y1: 16, x2: 6, y2: 16, stroke: colour, 'stroke-width': 1.5 }],
      ['line', { x1: 26, y1: 16, x2: 31, y2: 16, stroke: colour, 'stroke-width': 1.5 }]
    ];
    parts.forEach(function (part) {
      var el = document.createElementNS(SVG, part[0]);
      Object.keys(part[1]).forEach(function (k) { el.setAttribute(k, part[1][k]); });
      svg.appendChild(el);
    });
    return svg;
  }
  engine.mountCredit = function (credit) {
    if (!credit || !credit.text) { return; }
    var size = window.splitsmith.size;
    var theme = window.splitsmith.theme || {};
    var textSize = Math.round(size.height * 0.026);
    var wrap = document.createElement('div');
    wrap.className = 'credit';
    wrap.style.position = 'absolute';
    wrap.style.left = '0';
    wrap.style.right = '0';
    var bottom = Math.round(size.height * 0.04);
    var gap = Math.round(textSize * 0.5);
    wrap.style.bottom = bottom + 'px';
    wrap.style.display = 'flex';
    wrap.style.justifyContent = 'center';
    wrap.style.alignItems = 'center';
    wrap.style.gap = gap + 'px';
    wrap.appendChild(mark(Math.round(textSize * 1.2), theme.accent || '#e5484d'));
    var line = document.createElement('div');
    line.textContent = credit.text;
    line.style.fontFamily = '"Splitsmith Mono", monospace';
    line.style.fontSize = textSize + 'px';
    line.style.lineHeight = '1';
    line.style.color = theme.muted || '#9aa0a8';
    line.style.whiteSpace = 'nowrap';
    wrap.appendChild(line);
    // The cell is mounted first; take the credit's band off its bottom so a
    // crowded card fits its text above the line instead of under it.
    var cell = document.body.firstElementChild;
    document.body.appendChild(wrap);
    if (cell && cell !== wrap) {
      var reserve = bottom + Math.round(textSize * 1.2) + gap;
      cell.style.height = 'calc(' + (cell.style.height || '100%') + ' - ' + reserve + 'px)';
    }
  };
})();
