// The frames sting's helper (#1535): the pictures either side of the cut,
// one pair at a time. window.splitsmith.data.transition.frames names a URL
// per boundary frame for each edge; at(n) loads and decodes frame n's pair
// and closes the previous one, so a template never holds more than two
// decoded frames (all of a 4K boundary would be gigabytes). Without frames
// (the load the renderer makes to read sounds(), or looks check) count is
// 0 and every picture is null.
(function () {
  var data = (window.splitsmith && window.splitsmith.data) || {};
  var frames = (data.transition && data.transition.frames) || null;
  var held = [];

  function load(url) {
    if (!url) return Promise.resolve(null);
    var img = new Image();
    img.src = url;
    return img.decode().then(function () { return createImageBitmap(img); });
  }

  function at(n) {
    if (!frames || !frames.count) return Promise.resolve({ from: null, to: null });
    var i = Math.max(0, Math.min(frames.count - 1, n | 0));
    return Promise.all([load(frames.from[i]), load(frames.to[i])]).then(function (pair) {
      held.forEach(function (bitmap) { if (bitmap) bitmap.close(); });
      held = pair;
      return { from: pair[0], to: pair[1] };
    });
  }

  function draw(g, bitmap, x, y, w, h) {
    if (bitmap) g.drawImage(bitmap, x, y, w, h);
  }

  window.splitsmithFrames = { count: frames ? frames.count : 0, fps: frames ? frames.fps : 0, at: at, draw: draw };
})();
