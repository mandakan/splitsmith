// Your brand on a card (the branding work): the Look's logo, and its line
// beside it, as a corner mark, top-left, on the title page and the closing
// card. Top-right is the shooters' corner (identity.js) and the centre is
// the event's (event.js); the card's own text never moves for it.
// ``data.brand`` is only there for a Look that has a brand, on the cards
// that draw it, so every other card renders the same bytes as before this
// file existed. Mounted during DOMContentLoaded, before the load event the
// renderer waits for, like identity.js.
(function () {
  var engine = window.splitsmith.engine;
  engine.mountBrand = function (brand) {
    if (!brand || (!brand.logo && !brand.line)) { return; }
    var size = window.splitsmith.size;
    var theme = window.splitsmith.theme || {};
    var margin = Math.round(size.height * 0.04);
    var logoHeight = Math.round(size.height * 0.12);
    var lineSize = Math.round(size.height * 0.035);
    var wrap = document.createElement('div');
    wrap.className = 'brand';
    wrap.style.position = 'absolute';
    wrap.style.top = margin + 'px';
    wrap.style.left = margin + 'px';
    wrap.style.display = 'flex';
    wrap.style.alignItems = 'center';
    wrap.style.gap = Math.round(size.height * 0.02) + 'px';
    // Stop short of the shooters' corner.
    wrap.style.maxWidth = Math.round(size.width * 0.45) + 'px';
    if (brand.logo) {
      var img = document.createElement('img');
      img.src = brand.logo;
      img.alt = '';
      img.style.height = logoHeight + 'px';
      img.style.width = 'auto';
      img.style.maxWidth = (logoHeight * 2) + 'px';
      img.style.objectFit = 'contain';
      img.style.flex = 'none';
      wrap.appendChild(img);
    }
    if (brand.line) {
      var line = document.createElement('div');
      line.textContent = brand.line;
      line.style.fontFamily = '"Splitsmith Display", sans-serif';
      line.style.fontSize = lineSize + 'px';
      line.style.lineHeight = '1.1';
      line.style.color = theme.ink_2 || '#c9ccd2';
      line.style.whiteSpace = 'nowrap';
      line.style.overflow = 'hidden';
      line.style.textOverflow = 'ellipsis';
      line.style.minWidth = '0';
      wrap.appendChild(line);
    }
    document.body.appendChild(wrap);
  };
})();
