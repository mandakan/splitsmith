/* Shared parts for the Splitsmith hero + og:image renderers.
 *
 * Ported from the Splitsmith Design System bundle (hero-og/parts.jsx),
 * trimmed to remove the artboard wrapper background since each renderer
 * is its own page. The shot-envelope sequence below is fully
 * deterministic so the PNG is reproducible. */

const ARTBOARD_BG = {
  background:
    'radial-gradient(1200px 600px at 50% -120px, rgba(255,45,45,0.06), transparent 60%), ' +
    'linear-gradient(to bottom, #0E1014, #0A0B0D)',
  color: 'var(--ink)',
  position: 'relative',
  overflow: 'hidden',
  fontFamily: 'var(--font-sans)',
  letterSpacing: '-0.003em',
};

/* Brand mark -- canonical chronograph LED, copy of assets/logo-mark.svg. */
function BrandMark({ size = 36 }) {
  return (
    <svg viewBox="0 0 36 36" width={size} height={size} fill="none" aria-hidden="true">
      <rect x="1.5" y="1.5" width="33" height="33" rx="7" fill="#14171C" stroke="#3A4049" strokeWidth="1" />
      <rect x="10" y="8" width="3" height="20" rx="1.2" fill="#F4F4F5" />
      <rect x="23" y="8" width="3" height="20" rx="1.2" fill="#F4F4F5" />
      <circle cx="18" cy="18" r="2.4" fill="#FF2D2D" />
      <circle cx="18" cy="18" r="3.8" fill="none" stroke="#FF2D2D" strokeWidth="0.8" opacity="0.4" />
      <line x1="3" y1="32" x2="33" y2="32" stroke="#FF2D2D" strokeWidth="0.8" strokeDasharray="2 2" opacity="0.6" />
    </svg>
  );
}

function Wordmark({ size = 56, color = 'var(--ink)' }) {
  return (
    <div style={{
      fontFamily: 'var(--font-display)',
      fontWeight: 700,
      fontSize: size,
      lineHeight: 0.9,
      letterSpacing: '-0.02em',
      textTransform: 'uppercase',
      color,
    }}>Splitsmith</div>
  );
}

function Kicker({ children, color = 'var(--subtle)', size = 10, style }) {
  return (
    <div style={{
      fontFamily: 'var(--font-mono)',
      fontSize: size,
      fontWeight: 600,
      letterSpacing: '0.18em',
      textTransform: 'uppercase',
      color,
      fontFeatureSettings: 'var(--feat-num)',
      ...style,
    }}>{children}</div>
  );
}

/* Deterministic shot-envelope sequence -- pre-beep ambient, then 14
 * shot peaks each with attack + decay + quiet gap, then a tail. The
 * gap heights cycle (3,4,5,6) instead of using Math.random, so two
 * renders produce byte-identical PNGs. */
const WAVEFORM_HEIGHTS = (() => {
  const pre = [3, 4, 5, 7, 4, 3, 5, 8, 12, 16, 14, 10, 8, 6, 5, 4, 4, 5, 6, 8];
  const shots = [];
  const peaks = [96, 92, 88, 90, 86, 84, 88, 82, 78, 80, 74, 76, 72, 68];
  const gapCycle = [3, 4, 5, 6, 4, 3, 5, 6, 4, 5, 3, 6, 4, 5];
  peaks.forEach((p, i) => {
    shots.push(
      p,
      Math.round(p * 0.92),
      Math.round(p * 0.78),
      Math.round(p * 0.55),
      Math.round(p * 0.32),
      Math.round(p * 0.18),
      Math.round(p * 0.10),
      Math.round(p * 0.06),
    );
    const gap = i % 3 === 0 ? 5 : 4;
    for (let k = 0; k < gap; k++) {
      shots.push(gapCycle[(i + k) % gapCycle.length]);
    }
  });
  const post = [4, 3, 3, 2, 2, 2, 2, 2];
  return [...pre, ...shots, ...post];
})();

function Waveform({
  height = 110,
  showBeep = true,
  showShots = true,
  showRuler = true,
  showLabel = true,
  labelLeft = 'REC . STAGE 03 -- PER TOLD ME TO DO IT',
  labelRight = '14 shots . 14.74s . split avg 0.182',
  shotCount = 14,
  shotColor = 'var(--led)',
  barColor = 'rgba(255, 45, 45, 0.38)',
  inset = true,
  style = {},
}) {
  const shots = [];
  for (let i = 0; i < shotCount; i++) {
    const left = 20 + (i * (65 / Math.max(1, shotCount - 1)));
    shots.push({ i: String(i + 1).padStart(2, '0'), left });
  }
  const inner = (
    <div style={{
      position: 'relative',
      height,
      display: 'flex',
      alignItems: 'center',
      gap: 1,
      overflow: 'hidden',
    }}>
      {WAVEFORM_HEIGHTS.map((h, i) => (
        <div key={i} style={{
          flex: 1,
          background: barColor,
          borderRadius: 1,
          minWidth: 1,
          height: `${h}%`,
        }} />
      ))}
      {showBeep && (
        <div style={{
          position: 'absolute', top: 0, bottom: 0, left: '14%',
          borderLeft: '2px dashed var(--beep)',
          boxShadow: '0 0 10px var(--beep-glow)',
        }}>
          <div style={{
            position: 'absolute', top: -2, left: 6,
            fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600,
            color: 'var(--beep)', letterSpacing: '0.14em', textTransform: 'uppercase',
            whiteSpace: 'nowrap',
          }}>BEEP 0.000</div>
        </div>
      )}
      {showShots && shots.map(s => (
        <div key={s.i} style={{
          position: 'absolute', top: 6, bottom: 6, left: `${s.left}%`,
          borderLeft: `1.5px solid ${shotColor}`,
          boxShadow: '0 0 8px var(--led-glow)',
        }}>
          <div style={{
            position: 'absolute', bottom: -14, transform: 'translateX(-50%)',
            fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600,
            color: shotColor, letterSpacing: '0.08em',
          }}>{s.i}</div>
        </div>
      ))}
    </div>
  );

  const ruler = showRuler && (
    <div style={{
      display: 'flex', justifyContent: 'space-between',
      marginTop: 22,
      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--whisper)',
      fontFeatureSettings: 'var(--feat-num)',
    }}>
      <span>00.00</span><span>03.00</span><span>06.00</span>
      <span>09.00</span><span>12.00</span><span>15.00</span>
    </div>
  );

  if (!inset) {
    return <div style={style}>{inner}{ruler}</div>;
  }

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--rule)',
      borderRadius: 'var(--radius-2xl)',
      padding: '18px 20px 16px',
      boxShadow: 'var(--shadow-card)',
      ...style,
    }}>
      {showLabel && (
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
          letterSpacing: '0.18em', textTransform: 'uppercase',
          color: 'var(--subtle)', marginBottom: 14,
        }}>
          <span><span style={{ color: 'var(--led)' }}>{labelLeft.slice(0, 5)}</span>{labelLeft.slice(5)}</span>
          <span style={{ color: 'var(--muted)' }}>{labelRight}</span>
        </div>
      )}
      {inner}
      {ruler}
    </div>
  );
}

function Cell({ k, v, u, accent = 'var(--led)' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
        letterSpacing: '0.18em', textTransform: 'uppercase',
        color: 'var(--subtle)', marginBottom: 10,
      }}>{k}</div>
      <div style={{
        fontFamily: 'var(--font-display)', fontWeight: 700,
        fontSize: 44, lineHeight: 1, letterSpacing: '-0.01em',
        color: accent, textShadow: `0 0 16px ${accent === 'var(--led)' ? 'var(--led-glow)' : 'transparent'}`,
        fontFeatureSettings: 'var(--feat-num)',
      }}>
        {v}{u && <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 14,
          color: 'var(--muted)', marginLeft: 4, fontWeight: 500,
        }}>{u}</span>}
      </div>
    </div>
  );
}

function ChronoRule({ style }) {
  return (
    <div style={{
      height: 1,
      background:
        'linear-gradient(to right, transparent, var(--led) 18%, var(--led) 22%, ' +
        'var(--rule-strong) 30%, var(--rule-strong) 70%, ' +
        'var(--led) 78%, var(--led) 82%, transparent)',
      ...style,
    }} />
  );
}

Object.assign(window, { ARTBOARD_BG, BrandMark, Wordmark, Kicker, Waveform, Cell, ChronoRule });
