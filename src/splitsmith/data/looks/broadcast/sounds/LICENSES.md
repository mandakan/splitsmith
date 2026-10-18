# Broadcast Look sounds

The six files the Broadcast Look's cues play: the `sounds` block in its
`look.json` and the `window.sounds()` of its card, sting and summary
templates. Each is one CC0 1.0 sound from Freesound
(https://creativecommons.org/publicdomain/zero/1.0/), its licence checked
through the Freesound API when fetched (2026-10-18). Nothing here is CC-BY
or NC, so the Look names no `credits` file; attribution is given here
anyway.

Each was taken from Freesound's HQ preview, trimmed, faded and loudness
normalised with ffmpeg (`loudnorm` to -14 LUFS for the effects, -16 for the
music), then encoded to AAC, 48 kHz: the effects mono at 96 kbit/s, the
music stereo at 128 kbit/s. "Hit at" is where the file's loudest moment
sits, which a cue's `hit` names so that moment lands on its `at`.

| File | Hit at | Plays on | Made from |
|---|---|---|---|
| `whoosh.m4a` | 0.07 s | title page, slate, closing card, match summary | qubodup, Whoosh (60013), its first 10 ms of silence cut |
| `swoosh-soft.m4a` | 0.26 s | lower third, a sting's exit | Sadiquecat, Woosh - Metal tea strainer 4 - slowed down (742833), 0.10 to 0.95 s |
| `sting.m4a` | 0.59 s | a sting's sweep in | AudioPapkin, Cinematic Woosh SFX-011 (648732), 0.45 to 2.05 s |
| `tick.m4a` | 0.00 s | the stage summary counting up | Jummit, Soft UI Button Click (528561), its first 90 ms, 9 dB up |
| `impact.m4a` | 0.09 s | the stage summary landing | AudioPapkin, Sound Design Elements Impact SFX PS 043 (812709), 0.03 to 1.45 s |
| `theme.m4a` | (a bed) | under the title page and closing card | Lumamorph, Loop. An intense cinematic track for an active episode of your movie (716157), whole (17.5 s; a bed loops it) |

## Sources

| Source | Author | Licence | Page |
|---|---|---|---|
| Whoosh (60013) | qubodup | CC0 1.0 | https://freesound.org/people/qubodup/sounds/60013/ |
| Woosh - Metal tea strainer 4 - slowed down (742833) | Sadiquecat | CC0 1.0 | https://freesound.org/people/Sadiquecat/sounds/742833/ |
| Cinematic Woosh SFX-011 (648732) | AudioPapkin | CC0 1.0 | https://freesound.org/people/AudioPapkin/sounds/648732/ |
| Soft UI Button Click (528561) | Jummit | CC0 1.0 | https://freesound.org/people/Jummit/sounds/528561/ |
| Sound Design Elements Impact SFX PS 043 (812709) | AudioPapkin | CC0 1.0 | https://freesound.org/people/AudioPapkin/sounds/812709/ |
| Loop. An intense cinematic track for an active episode of your movie (716157) | Lumamorph | CC0 1.0 | https://freesound.org/people/Lumamorph/sounds/716157/ |
