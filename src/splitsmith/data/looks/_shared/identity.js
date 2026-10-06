// Per-shooter identity on a card (#1243): the logos of the shooters that
// have one, top-right, sized to the canvas. Appended outside the cell so
// the cell's markup stays what overlay_html builds (the parity test in
// tests/test_look_template.py compares exactly that), and appended only
// when there is a logo to show, so a card without identities renders the
// same bytes as before this file existed. Images are added during
// DOMContentLoaded, before the load event, so the renderer's wait for
// load covers them.
(function () {
  var engine = window.splitsmith.engine;
  engine.mountIdentity = function (shooters, slot) {
    var withLogo = (shooters || []).filter(function (s) { return s && s.logo; });
    if (!withLogo.length) { return; }
    var size = window.splitsmith.size;
    var logoHeight = Math.round(size.height * 0.12);
    var margin = Math.round(size.height * 0.04);
    var wrap = document.createElement('div');
    wrap.className = 'identity-logos identity-logos-' + slot;
    wrap.style.position = 'absolute';
    wrap.style.top = margin + 'px';
    wrap.style.right = margin + 'px';
    wrap.style.display = 'flex';
    wrap.style.alignItems = 'center';
    wrap.style.gap = Math.round(logoHeight / 3) + 'px';
    withLogo.forEach(function (shooter) {
      var img = document.createElement('img');
      img.src = shooter.logo;
      img.alt = '';
      img.style.height = logoHeight + 'px';
      img.style.width = 'auto';
      img.style.maxWidth = (logoHeight * 2) + 'px';
      img.style.objectFit = 'contain';
      wrap.appendChild(img);
    });
    document.body.appendChild(wrap);
  };
})();
