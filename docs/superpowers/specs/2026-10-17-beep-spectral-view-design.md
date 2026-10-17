# Beep spectral view and tone check

Date: 2026-10-17. Status: approved in conversation, spec for review.

## Why

Many stages in the 2026 corpus have a cross-bay shot landing on the beep.
The beep step's Snap and its "N ms before/after the tone's start" line
both run Snap's rule, ``rise_foot``, on the full-range envelope
(``BeepTimeline``: ``snapToLeadingEdge`` over the 1 ms detail peaks from
``GET .../videos/{id}/peaks?start&end``). Under a shot that envelope
rises with the shot, not the tone. So Snap pulls a correct beep onto the
shot, and the distance line warns about a beep that is right.

The offline beep scan made the same mistake. On 2026-10-17 it flagged
nine promoted stages (Bofors, Hugelsta, Little Trouble, Oden Cup) by
20 ms or more. Some of those are the shot, not the beep.

A timer's tone is near-pure, with fundamentals of 2.3 to 3.2 kHz on the
labelled fixtures. ``BeepDetectionConfig.tonal_band_lo_hz`` /
``tonal_band_hi_hz`` already hold 2200 to 3500 Hz. Shots and steel
spread their energy across the whole 2 to 5 kHz band and below. Looking
in the tone's band separates the two. Seeing frequency makes the overlap
obvious at a glance.

## What the user gets

On Audit's beep step (desktop) the band gains a few things. The beep step
is also what Re-pick from step 2 opens, and it is where secondary angles'
beeps are confirmed, so all three get them.

- **A Spectrogram row** under Candidates: time against frequency, 0 to
  8 kHz, with the timer band outlined. It sits on the same ruler and
  zoom as the Audio row and is seekable like it.
- **A Filter control** in the band's header: Off, Timer tone (2.2 to
  3.5 kHz) and Custom (low and high in Hz). The choice is remembered per
  browser. Timer tone is the default when the stage has a spectrogram.
  With a filter on:
  - the Audio row draws the band-passed waveform;
  - Snap becomes "Snap to tone" and finds the tone's rise, not the
    shot's;
  - the distance line measures against the tone.
- **The distance line** keeps its place and wording ("51 ms after the
  tone starts"). With a filter on it is measured in the band. The user
  may still confirm the beep where it is: their eye wins over the
  measurement.

On the Overview, a stage row whose primary or any secondary has a
confirmed beep 20 ms or more from its tone's start shows a **Check beep**
chip. It is amber with its label, so colour is never the only cue, and
it links to that stage's beep step.

Not in scope:
- the phone's beep review (``MobileBeepReview``), which could show the
  same flag later;
- the lab;
- a promote guard;
- the overlay.

## Design

### One measurement, in Python

``splitsmith.beep_tone``, new and pure, with no I/O:

- ``band_pass(audio, sr, lo_hz, hi_hz) -> np.ndarray``: a zero-phase
  Butterworth band-pass (``scipy.signal.sosfiltfilt``, already a
  dependency through librosa). Zero-phase, because a phase shift would
  move the rise it is meant to find.
- ``tone_offset_ms(audio, sr, beep_s, band) -> float | None``: band-pass
  the seconds around the beep, then run ``rise_foot.peak_envelope`` and
  ``rise_foot.rise_foot`` with Snap's 100 ms reach. Returns the beep's
  distance from the foot in ms (positive means the beep is before the
  tone), or ``None`` when there is no edge within reach. The same window
  and normalisation as the offline scan, so the numbers compare.
- ``spectrogram(audio, sr, start_s, end_s) -> Spectrogram``:
  - an STFT with a 1024-sample window and a hop for about 5 ms columns
    at the WAV's rate;
  - magnitude in dB clipped to an 80 dB range and quantised to uint8;
  - bins from 0 to 8 kHz only;
  - plus the column times and the frequency of each bin.

``BeepToneConfig`` in ``config.py`` holds the band (defaulting to
``BeepDetectionConfig``'s tonal band), the 20 ms flag threshold and the
spectrogram window. All of it is data, overridable through
``SPLITSMITH_CONFIG``.

### Routes

- **``GET .../videos/{id}/spectrum?start=&end=``** returns ``{start,
  duration, hz: [...], columns, data: <base64 uint8>}`` for one window of
  the video's own audit WAV. The window is capped at 4 s.
  - It reads the published WAV only (``published_video_audio``). When
    that is missing it queues the ``derive_audio`` job and answers 202
    ``pending``, exactly as the peaks route does. A request never runs
    ffmpeg.
  - It is on the share GET allowlist only if the peaks route is; it is
    not added otherwise.
- **The existing ``GET .../videos/{id}/peaks``** takes an optional
  ``band=LO-HI``, validated as 100 to 12000 Hz with LO < HI, else 422.
  With a band, the peaks are of the band-passed window. The detail fetch
  passes the active filter, so Snap and the distance line run on the
  band without new code in the browser.
- **``POST .../beep/snap``** (the server snap, used by the hosted phone
  path) takes the same optional band. Its default stays as it is.

### Stored offset, read by the Overview

``StageVideo.beep_tone_offset_ms: float | None`` holds the measured
offset of the confirmed beep. It is never computed per request.

- It is written whenever a beep is confirmed or moved. That is
  ``_after_beep_reviewed`` (which already holds the WAV path) and the
  manual beep override.
- It is cleared when the beep becomes unreviewed.
- The trim job writes it too, once the audit WAV exists, for a confirm
  that ran before its WAV.
- It is a project field and syncs with the project doc like
  ``beep_confidence``. An older install ignores the unknown key, since
  project models already ignore extras; check this, and add it to the
  model's tolerated fields if they do not.
- ``scripts/measure_beep_tones.py`` backfills every confirmed beep in
  the matches on X9: a dry run by default, ``--write`` saves. It prints
  the full-range and the band-limited offsets side by side, which is
  also the check on the nine stages flagged by the old scan.

The Overview reads the stored value: ``lib/overview`` gains a
``checkBeep`` per stage row when any video's ``|beep_tone_offset_ms| >=``
the threshold. The threshold rides the project payload from the config,
so the SPA has no literal of its own.

### SPA

- **``components/audit/SpectrogramTrack``** draws the visible window of
  the uint8 grid into a viewport-sized canvas, never a full-content one,
  the same rule as ``WaveformTrack``.
  - Its colour map is perceptually uniform, from dark to light. It
    reads in greyscale, so it does not depend on colour.
  - The timer band is drawn as two hairlines with a label.
  - It fetches the spectrum for the band's visible window around the
    beep, plus a margin, and fetches again when the beep or the zoom
    moves the window out of what it holds.
- **The Filter control** is a ``Segmented`` (Off / Timer tone / Custom)
  in the band's header. Custom opens two number fields. The rules live
  in pure ``lib/beepFilter``: the presets, parsing, validation, and
  storage per browser through try/catch.
- ``BeepTimeline`` passes the filter to the detail fetch. Its Snap
  button's label becomes "Snap to tone" when a filter is on.
- On the Overview, the chip is a ``rowAction`` case in ``lib/overview``
  that links to the stage's beep step.

## Testing

- **``beep_tone``, on real fixtures.** At least one cross-bay stage from
  today's list, such as Little Trouble stage 2 or Hugelsta stage 3,
  where the full-range foot is the shot and the band-limited foot is
  the tone. The case is chosen by listening and looking first, so the
  expected values come from the audio, not from the code. Also a clean
  beep where both agree within 5 ms. The fixture WAVs these tests read
  are added to ``tests/fixtures/audio.ci.txt``.
- **Routes:**
  - spectrum shape and window cap;
  - 202 and the queued job when the WAV is missing;
  - band validation (422);
  - peaks with and without a band differ on the overlap stage.
- **Stored offset:**
  - written on confirm and on override;
  - cleared on unreview;
  - the backfill's dry run writes nothing.
- **SPA:**
  - the ``beepFilter`` rules;
  - the track draws only the visible window;
  - the filter reaches the detail fetch;
  - the label switches;
  - the Overview chip appears at the threshold and not under it.
- **In a browser:** the beep step with a seeded overlap stage, looked
  at, not just tested.

## Open

- Whether Timer tone should be the default filter. The proposal says
  yes when the spectrogram loads. A timer outside the band (none in the
  corpus so far) would then need Custom; ``docs/cameras.md``-style notes
  would record it.
