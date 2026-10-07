// Your brand on a card (the branding work): the Look's logo, and its line
// under it, as the centrepiece of the title page and the closing card. The
// card's own text moves into the space below, so the two never overlap.
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
    // The brand block takes the top part of the canvas; the cell below it
    // keeps its own centring in what is left.
    var top = Math.round(size.height * 0.08);
    var logoHeight = Math.round(size.height * 0.24);
    var lineSize = Math.round(size.height * 0.04);
    var gap = Math.round(size.height * 0.02);
    var block = 0;
    var wrap = document.createElement('div');
    wrap.className = 'brand';
    wrap.style.position = 'absolute';
    wrap.style.top = top + 'px';
    wrap.style.left = '0';
    wrap.style.right = '0';
    wrap.style.display = 'flex';
    wrap.style.flexDirection = 'column';
    wrap.style.alignItems = 'center';
    wrap.style.gap = gap + 'px';
    if (brand.logo) {
      var img = document.createElement('img');
      img.src = brand.logo;
      img.alt = '';
      img.style.height = logoHeight + 'px';
      img.style.width = 'auto';
      img.style.maxWidth = Math.round(size.width * 0.5) + 'px';
      img.style.objectFit = 'contain';
      wrap.appendChild(img);
      block += logoHeight;
    }
    if (brand.line) {
      var line = document.createElement('div');
      line.textContent = brand.line;
      line.style.fontFamily = '"Splitsmith Display", sans-serif';
      line.style.fontSize = lineSize + 'px';
      line.style.lineHeight = '1.1';
      line.style.color = theme.ink_2 || '#c9ccd2';
      line.style.whiteSpace = 'nowrap';
      wrap.appendChild(line);
      block += (brand.logo ? gap : 0) + Math.round(lineSize * 1.1);
    }
    document.body.appendChild(wrap);
    // Push the card's text below the brand block.
    var offset = top + block + gap * 2;
    var cell = document.body.firstElementChild;
    if (cell && cell !== wrap) {
      cell.style.marginTop = offset + 'px';
      cell.style.height = 'calc(100% - ' + offset + 'px)';
    }
  };
})();
