// The event's own logo on a card (the branding work): the centrepiece of the
// title page and the closing card, above the match name. The card's own text
// moves into the space below, so the two never overlap. Top-left is your
// brand (brand.js) and top-right the shooters' corner (identity.js).
// ``data.event`` is only there when the match has an event logo, on the
// cards that draw it, so every other card renders the same bytes as before.
//
// A logo is never drawn larger than its own pixels (#1433): ``scale-down``
// draws one smaller than its slot at its own size, centred in the slot, and
// one at least as large exactly as ``contain`` did. A corner mark the logo
// would touch (a wide wordmark on an upright card, #1431) is settled once
// the images have loaded, before the card's fit: the corner shrinks by its
// own aspect into the room beside the logo, or, when that room is under
// half of what it draws now, the logo and the text move down below the
// corners. A card whose marks do not touch is left exactly as it was.
(function () {
  var engine = window.splitsmith.engine;
  // The drawn picture of an <img>, not its box: ``contain`` and
  // ``scale-down`` letterbox inside the box, centred.
  function contentRect(img) {
    var box = img.getBoundingClientRect();
    var nw = img.naturalWidth, nh = img.naturalHeight;
    if (!(nw > 0 && nh > 0) || !(box.width > 0 && box.height > 0)) { return box; }
    var fit = getComputedStyle(img).objectFit;
    var s = Math.min(box.width / nw, box.height / nh);
    if (fit === 'scale-down') { s = Math.min(s, 1); }
    var w = nw * s, h = nh * s;
    var left = box.left + (box.width - w) / 2, top = box.top + (box.height - h) / 2;
    return {left: left, top: top, right: left + w, bottom: top + h, width: w, height: h};
  }
  function textRect(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    return range.getBoundingClientRect();
  }
  // What a corner mark draws: its pictures and its line.
  function markRect(wrap) {
    var out = null;
    Array.prototype.forEach.call(wrap.children, function (child) {
      var r = child.tagName === 'IMG' ? contentRect(child) : textRect(child);
      if (!(r.width > 0 && r.height > 0)) { return; }
      out = out ? {
        left: Math.min(out.left, r.left), top: Math.min(out.top, r.top),
        right: Math.max(out.right, r.right), bottom: Math.max(out.bottom, r.bottom)
      } : {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
    });
    return out;
  }
  function touches(a, b, gap) {
    return a.left < b.right + gap && b.left < a.right + gap && a.top < b.bottom + gap && b.top < a.bottom + gap;
  }
  // Your brand keeps its left edge and ends ``room`` px later: the logo is
  // capped to the room, the line beside it takes what is left.
  function shrinkBrand(wrap, room) {
    wrap.style.maxWidth = room + 'px';
    Array.prototype.forEach.call(wrap.querySelectorAll('img'), function (img) {
      var current = parseFloat(img.style.maxWidth) || Infinity;
      img.style.maxWidth = Math.min(current, room) + 'px';
    });
  }
  // The shooters' logos keep the right edge and shrink together, by one
  // factor, to ``room`` px including their gaps.
  function shrinkIdentity(wrap, room, drawn) {
    var imgs = Array.prototype.slice.call(wrap.querySelectorAll('img'));
    var gap = parseFloat(getComputedStyle(wrap).columnGap) || 0;
    var gaps = gap * Math.max(0, imgs.length - 1);
    var factor = Math.max(0, (room - gaps) / Math.max(1, drawn - gaps));
    imgs.forEach(function (img) {
      img.style.height = parseFloat(img.style.height) * factor + 'px';
      img.style.maxWidth = parseFloat(img.style.maxWidth) * factor + 'px';
    });
  }
  function settle(img, cell, gap) {
    var event = contentRect(img);
    if (!(event.width > 0)) { return; }
    var push = 0;
    [['brand', document.querySelector('.brand')], ['identity', document.querySelector('.identity-logos')]]
      .forEach(function (corner) {
        var wrap = corner[1];
        if (!wrap) { return; }
        var mark = markRect(wrap);
        if (!mark || !touches(mark, event, gap)) { return; }
        var drawn = mark.right - mark.left;
        var room = corner[0] === 'brand' ? event.left - gap - mark.left : mark.right - (event.right + gap);
        if (room >= drawn / 2) {
          if (corner[0] === 'brand') { shrinkBrand(wrap, room); } else { shrinkIdentity(wrap, room, drawn); }
        } else {
          push = Math.max(push, mark.bottom + gap - event.top);
        }
      });
    if (push > 0) {
      push = Math.ceil(push);
      img.style.top = parseFloat(img.style.top) + push + 'px';
      if (cell && cell !== img) {
        cell.style.marginTop = parseFloat(cell.style.marginTop) + push + 'px';
        cell.style.height = 'calc(' + cell.style.height + ' - ' + push + 'px)';
      }
    }
  }
  engine.mountEvent = function (event) {
    if (!event || !event.logo) { return; }
    var size = window.splitsmith.size;
    var top = Math.round(size.height * 0.08);
    var logoHeight = Math.round(size.height * 0.24);
    var gap = Math.round(size.height * 0.02);
    var cell = document.body.firstElementChild;
    var img = document.createElement('img');
    img.className = 'event-logo';
    img.src = event.logo;
    img.alt = '';
    img.style.position = 'absolute';
    img.style.top = top + 'px';
    img.style.left = '50%';
    img.style.transform = 'translateX(-50%)';
    img.style.height = logoHeight + 'px';
    img.style.width = 'auto';
    img.style.maxWidth = Math.round(size.width * 0.5) + 'px';
    img.style.objectFit = 'scale-down';
    document.body.appendChild(img);
    // Push the card's text below the logo.
    var offset = top + logoHeight + gap * 2;
    if (cell && cell !== img) {
      cell.style.marginTop = offset + 'px';
      cell.style.height = 'calc(100% - ' + offset + 'px)';
    }
    // The renderer calls the fit once the images have loaded, and only
    // then are the logos' own sizes known: settle the corners first.
    var fit = window.__splitsmithFit;
    window.__splitsmithFit = function () {
      settle(img, cell, gap);
      return fit ? fit.apply(this, arguments) : undefined;
    };
  };
})();
