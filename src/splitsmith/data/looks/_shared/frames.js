// The frames sting's helper (#1535): the pictures either side of the cut,
// one pair at a time. window.splitsmith.data.transition.frames names a URL
// per boundary frame for each edge. Contract: at(n) resolves the decoded pair
// for frame n; only one pair is decoded at a time, and a pair is valid until
// the next at() call, which closes it first (draw before you call at() again;
// all of a 4K boundary would be gigabytes). Calls are serialised, so a second
// at() waits for the first to settle. Without frames (the load the renderer
// makes to read sounds(), or looks check) count is 0 and every picture is null.
(function () {
  var data = (window.splitsmith && window.splitsmith.data) || {};
  var frames = (data.transition && data.transition.frames) || null;
  var held = [];
  var tail = Promise.resolve();

  function load(url) {
    if (!url) return Promise.resolve(null);
    var img = new Image();
    img.src = url;
    return img.decode().then(function () { return createImageBitmap(img); });
  }

  function release() {
    held.forEach(function (bitmap) { if (bitmap) bitmap.close(); });
    held = [];
  }

  function fetchPair(i) {
    release();
    var results = [null, null];
    var loads = [frames.from[i], frames.to[i]].map(function (url, k) {
      return load(url).then(function (bitmap) { results[k] = bitmap; });
    });
    return Promise.allSettled(loads).then(function (settled) {
      var failed = settled.filter(function (s) { return s.status === 'rejected'; })[0];
      if (failed) {
        results.forEach(function (bitmap) { if (bitmap) bitmap.close(); });
        throw failed.reason;
      }
      held = results;
      return { from: results[0], to: results[1] };
    });
  }

  function at(n) {
    if (!frames || !frames.count) return Promise.resolve({ from: null, to: null });
    var i = Math.max(0, Math.min(frames.count - 1, n | 0));
    var run = tail.then(function () { return fetchPair(i); });
    tail = run.catch(function () {});
    return run;
  }

  function draw(g, bitmap, x, y, w, h) {
    if (bitmap) g.drawImage(bitmap, x, y, w, h);
  }

  window.splitsmithFrames = { count: frames ? frames.count : 0, fps: frames ? frames.fps : 0, at: at, draw: draw };
})();
