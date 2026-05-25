/* OgA -- "Brand plate" at 1200 x 630. Ported from the Splitsmith
 * Design System hero-og/ogs.jsx. Centered mark + wordmark + tagline,
 * thin waveform near the bottom. Reads as a 200px Twitter thumbnail. */

function OgA() {
  return (
    <div style={{ ...ARTBOARD_BG, width: 1200, height: 630, padding: '64px 72px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
      <Kicker size={11} color="var(--subtle)" style={{ marginBottom: 24 }}>open-source . python + react</Kicker>
      <div style={{ display: 'flex', alignItems: 'center', gap: 22 }}>
        <BrandMark size={104} />
        <Wordmark size={108} />
      </div>
      <div style={{
        fontFamily: 'var(--font-display)', fontWeight: 700,
        fontSize: 46, lineHeight: 1, letterSpacing: '-0.005em',
        color: 'var(--ink)', textTransform: 'none', marginTop: 28,
      }}>
        Detect. Coach. <span style={{ color: 'var(--led)' }}>Cut.</span>
      </div>
      <div style={{
        fontFamily: 'var(--font-sans)', fontSize: 17,
        color: 'var(--muted)', marginTop: 14, maxWidth: 640, lineHeight: 1.5,
      }}>
        Per-shot split detection from head-mounted IPSC footage. CSV + FCPXML out.
      </div>

      <div style={{ position: 'absolute', left: 64, right: 64, bottom: 56 }}>
        <Waveform
          inset={false}
          height={28}
          showBeep={false}
          showLabel={false}
          showRuler={false}
          shotCount={14}
          barColor="rgba(255, 45, 45, 0.30)"
        />
        <ChronoRule style={{ marginTop: 14, opacity: 0.7 }} />
      </div>
    </div>
  );
}

Object.assign(window, { OgA });
