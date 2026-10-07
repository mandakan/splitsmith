// The event's own logo on a card (the branding work): the centrepiece of the
// title page and the closing card, above the match name. The card's own text
// moves into the space below, so the two never overlap. Top-left is your
// brand (brand.js) and top-right the shooters' corner (identity.js).
// ``data.event`` is only there when the match has an event logo, on the
// cards that draw it, so every other card renders the same bytes as before.
(function () {
  var engine = window.splitsmith.engine;
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
    img.style.objectFit = 'contain';
    document.body.appendChild(img);
    // Push the card's text below the logo.
    var offset = top + logoHeight + gap * 2;
    if (cell && cell !== img) {
      cell.style.marginTop = offset + 'px';
      cell.style.height = 'calc(100% - ' + offset + 'px)';
    }
  };
})();
