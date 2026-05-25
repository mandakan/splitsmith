/* HeroA -- "Telemetry hero" at 1600 x 640. Ported from the Splitsmith
 * Design System hero-og/heroes.jsx. The only chrome tweak vs the
 * design canvas is that this renderer is its own full-bleed page, so
 * the artboard background fills the whole document. */

function HeroA() {
  return (
    <div style={{ ...ARTBOARD_BG, width: 1600, height: 640, padding: '52px 64px', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <BrandMark size={44} />
          <Wordmark size={32} />
          <div style={{ width: 1, height: 26, background: 'var(--rule)', margin: '0 8px' }} />
          <Kicker size={11} color="var(--muted)">v0.2.1 . MIT . IPSC Production Optics</Kicker>
        </div>
        <Kicker size={11} color="var(--led)">github . mandakan/splitsmith</Kicker>
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginTop: 28, gap: 48 }}>
        <div>
          <Kicker size={11} color="var(--subtle)" style={{ marginBottom: 12 }}>chronograph for splits</Kicker>
          <div style={{
            fontFamily: 'var(--font-display)', fontWeight: 700,
            fontSize: 76, lineHeight: 0.95, letterSpacing: '-0.01em',
            color: 'var(--ink)', textTransform: 'none',
          }}>
            Detect. Coach.<br />
            <span style={{ color: 'var(--led)', textShadow: '0 0 24px var(--led-glow)' }}>Cut.</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 36, paddingBottom: 6 }}>
          <Cell k="Voters" v="3" />
          <Cell k="Typical split" v="0.18" u="s" />
          <Cell k="Beep tol" v="15" u="ms" />
          <Cell k="Output" v="FCPXML" accent="var(--done)" />
        </div>
      </div>

      <div style={{ marginTop: 'auto' }}>
        <Waveform
          height={84}
          labelLeft="REC . STAGE 03 -- PER TOLD ME TO DO IT"
          labelRight="14 shots . 14.74s . split avg 0.182"
        />
      </div>
    </div>
  );
}

Object.assign(window, { HeroA });
