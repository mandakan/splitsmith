// The helpers every shipped HUD style shares (Plate, Pips, Ticker,
// Timeline, Minimal): easing, colours, and the stage-event pieces -- the
// stage bar's bands, the reload chip and how long the chip keeps the HUD
// moving after the last shot. A template calls
// ``window.splitsmithHud(window.splitsmith)`` once and draws the rest
// itself. Nothing here reads the wall clock or touches the page until a
// template asks it to build something, so loading it changes no frame.
//
// Confirmed regions only reach the data; with a toggle off, or nothing to
// draw, no element is created and the page is exactly the plain one.
(function () {
  window.splitsmithHud = function (s) {
    var theme = s.theme, stage = s.data.stage, opts = s.data.options;
    var shots = stage.shots, beep = stage.beep;
    var span = stage.stage_time > 0 ? stage.stage_time : 1;
    var CHIP_FADE = 0.4;
    var reloads = opts.reload_chip ? stage.reloads : [];
    var regions = opts.stage_bar ? stage.events : [];

    function clamp(x) { return Math.max(0, Math.min(1, x)); }
    function easeOut(x) { x = clamp(x); return 1 - Math.pow(1 - x, 3); }
    function backOut(x) { x = clamp(x); var c = 1.9; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); }
    function rgba(hex, a) {
      var n = parseInt(hex.slice(1), 16);
      return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
    }
    function tierColour(tier) {
      if (!opts.speed_colors || !tier) return theme.split;
      return tier === 'good' ? theme.split_good : tier === 'slow' ? theme.accent : theme.split;
    }
    // Percent along the stage of clip time x, 0 at the beep.
    function along(x) { return 100 * clamp((x - beep) / span); }
    function regionColour(kind) {
      return kind === 'movement' ? theme.movement : kind === 'reload' ? theme.reload : theme.muted;
    }
    // The bar's pieces: movement and activation whole, then each reload cut
    // where it meets a movement -- that slice takes the bar's top half, so
    // the movement shows under it and the bar says the reload was on the
    // move. A piece that meets another of the same reload is square on that
    // side, so the reload reads as one band with a step in it.
    function pieces(regions) {
      var out = [], moves = [];
      regions.forEach(function (r) {
        if (r.kind === 'reload') return;
        out.push({kind: r.kind, start: r.start, end: r.end, half: false});
        if (r.kind === 'movement') moves.push(r);
      });
      regions.forEach(function (r) {
        if (r.kind !== 'reload') return;
        var cuts = moves
          .filter(function (m) { return m.start < r.end && r.start < m.end; })
          .map(function (m) { return [Math.max(r.start, m.start), Math.min(r.end, m.end)]; })
          .sort(function (a, b) { return a[0] - b[0]; });
        var from = r.start;
        cuts.forEach(function (c) {
          if (c[0] > from) {
            out.push({kind: 'reload', start: from, end: c[0], half: false, joinL: from > r.start, joinR: true});
          }
          if (c[1] > from) {
            var s = Math.max(from, c[0]);
            out.push({kind: 'reload', start: s, end: c[1], half: true, joinL: s > r.start, joinR: c[1] < r.end});
            from = c[1];
          }
        });
        if (from < r.end) {
          out.push({kind: 'reload', start: from, end: r.end, half: false, joinL: from > r.start, joinR: false});
        }
      });
      return out;
    }
    // One ``.band`` element per piece, appended to ``parent`` (a track the
    // template positions); ``seek(running)`` grows each with the clock, so
    // the track never shows a region before it happens.
    function regionBands(parent) {
      var spans = pieces(regions);
      var bands = spans.map(function (region) {
        var band = document.createElement('div');
        band.className = 'band';
        band.dataset.kind = region.kind;
        band.style.left = along(region.start) + '%';
        band.style.background = regionColour(region.kind);
        band.style.opacity = 0.9;
        if (region.half) band.style.bottom = '50%';
        if (region.joinL) { band.style.borderTopLeftRadius = 0; band.style.borderBottomLeftRadius = 0; }
        if (region.joinR) { band.style.borderTopRightRadius = 0; band.style.borderBottomRightRadius = 0; }
        parent.appendChild(band);
        return band;
      });
      return {
        seek: function (running) {
          for (var b = 0; b < bands.length; b++) {
            var width = along(Math.min(spans[b].end, beep + running)) - along(spans[b].start);
            bands[b].style.width = Math.max(0, width) + '%';
            bands[b].style.visibility = width > 0 ? 'visible' : 'hidden';
          }
        },
      };
    }
    // The stage bar (``#stageBar``): a track that fills with time
    // (``#stageFill``), the bands drawn over the fill as it reaches them.
    // Null when there is nothing; the template places ``el``.
    function stageBar() {
      if (!pieces(regions).length) return null;
      var bar = document.createElement('div');
      bar.id = 'stageBar';
      bar.style.background = rgba(theme.ink, 0.22);
      var fill = document.createElement('div');
      fill.id = 'stageFill';
      fill.style.background = theme.ink;
      bar.appendChild(fill);
      var bands = regionBands(bar);
      return {
        el: bar,
        seek: function (running) {
          fill.style.width = 100 * clamp(running / span) + '%';
          bands.seek(running);
        },
      };
    }
    // The reload chip: counts up while the reload runs, then holds the
    // reload's duration while it fades. ``build()`` makes and places the
    // element (its last child takes the figure); it is given ``id``
    // (default ``reloadChip``). Null when there is no reload.
    function reloadChip(build, id) {
      if (!reloads.length) return null;
      var chip = build();
      chip.id = id || 'reloadChip';
      return {
        el: chip,
        seek: function (t) {
          var live = null;
          for (var j = 0; j < reloads.length; j++) {
            if (reloads[j].start <= t && t < reloads[j].end + CHIP_FADE) live = reloads[j];
          }
          if (!live) { chip.style.opacity = 0; return; }
          var held = t >= live.end;
          chip.style.opacity = held ? clamp(1 - (t - live.end) / CHIP_FADE) : 1;
          chip.lastChild.textContent = (held ? live.duration : t - live.start).toFixed(2);
        },
      };
    }
    // A reload that ends after the last shot keeps the chip counting and
    // fading past it; the frame plan holds whatever the clip cannot reach.
    function chipSettle(base) {
      var lastT = shots.length ? shots[shots.length - 1].t : beep;
      for (var j = 0; j < reloads.length; j++) base = Math.max(base, reloads[j].end + CHIP_FADE - lastT);
      return base;
    }

    return {
      span: span,
      clamp: clamp,
      easeOut: easeOut,
      backOut: backOut,
      rgba: rgba,
      tierColour: tierColour,
      along: along,
      regionColour: regionColour,
      pieces: pieces,
      regionBands: regionBands,
      stageBar: stageBar,
      reloadChip: reloadChip,
      chipSettle: chipSettle,
    };
  };
})();
