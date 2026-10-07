// The event's own logo on a card (the branding work): a corner mark,
// top-left, on the title page and the closing card. Top-right is the
// shooters' corner (identity.js) and the centre is your brand (brand.js).
// ``data.event`` is only there when the match has an event logo, on the
// cards that draw it, so every other card renders the same bytes as before.
(function () {
  var engine = window.splitsmith.engine;
  engine.mountEvent = function (event) {
    if (!event || !event.logo) { return; }
    var size = window.splitsmith.size;
    var logoHeight = Math.round(size.height * 0.12);
    var margin = Math.round(size.height * 0.04);
    var img = document.createElement('img');
    img.className = 'event-logo';
    img.src = event.logo;
    img.alt = '';
    img.style.position = 'absolute';
    img.style.top = margin + 'px';
    img.style.left = margin + 'px';
    img.style.height = logoHeight + 'px';
    img.style.width = 'auto';
    img.style.maxWidth = (logoHeight * 2) + 'px';
    img.style.objectFit = 'contain';
    document.body.appendChild(img);
  };
})();
