# Shot detection landscape review, September 2026

Research only. No code changed. This document records what the shipped
shot detection and classification pipeline does, what the market and the
literature have produced since the project started, which of it is usable
under a commercial licence, and what is worth trying next. Every claim
about an external model or product comes from a source listed at the end;
where a source could not be reached the text says so.

## 1. Verdict in short

1. **The pipeline's structure is still sound.** Envelope onsets as the
   candidate generator, half-rise for timing, and a small trained
   classifier over per-candidate features is what the best-documented
   systems in the field still do. Nothing published since 2025 makes a
   large audio model a better *timer* than a physical onset definition.
   Large audio-language models in particular are poor at timestamps
   (best system on TAG-Bench: 31.2 mIoU).
2. **The classifier's inputs are dated.** PANN CNN14 (2020, 0.431 mAP on
   AudioSet) and LAION CLAP-HTSAT (2023, 0.463) are the two learned
   feature sources. Self-supervised encoders released 2024 to 2026 reach
   0.486 to 0.502 mAP at the same 88M parameter size, several under MIT
   or Apache 2.0. The 2026-05-11 dashboard findings already reduced the
   design to "add a feature column to voter C and retrain", so swapping
   the embedding source is a contained change.
3. **The evaluation has a leak that must be fixed before any model
   comparison means anything.** Voter C is trained on all calibration
   candidates and its threshold picked by a shuffled, candidate-level
   5-fold split. Candidates from the same stage, and the same shot
   recorded by two cameras, fall in different folds. The headline
   F1 0.971 is therefore in-sample for voter C. Grouped folds by
   (match, stage) or a held-out match are needed first.
4. **The corpus is the lever, not the model.** 30 fixtures, 576 labelled
   shots, two matches, two shooters, three camera models. Every finding
   in ``docs/ensemble_dashboard/findings`` says the classifier is where
   the leverage is, and a classifier is only as broad as its data.
   Public gunshot datasets (two of them CC BY 4.0) help as hard
   negatives, not as a replacement: none of them are shooter-worn
   cameras with AGC ducking and neighbouring bays.
5. **Three products now do ML shot detection from video** (Shot Streamer,
   Shooting Cut, Shooters Global's Shotisize AI). None publishes its
   method or an audited error rate. Splitsmith's audit trail, calibrated
   per-camera thresholds and open evaluation corpus are the
   differentiators; a better classifier keeps them meaningful.
6. **Licensing is clean for the recommended path and dirty for three
   tempting shortcuts.** EAT, SSLAM, FlexSED, BEATs, OpenBEATs, Dasheng,
   Qwen3-Omni and V-JEPA 2 are usable commercially. M2D (NTT
   evaluation-only), FLAM (Adobe non-commercial) and Audio Flamingo 3
   (NVIDIA non-commercial) are not, however good their numbers look.

## 2. What ships today

Source: ``src/splitsmith/ensemble``, ``shot_detect.py``,
``docs/METHODOLOGY.md``, ``docs/ensemble_dashboard/latest_report.md``,
``src/splitsmith/data/ensemble_calibration.json`` (built 2026-08-17).

- **Beep**: bandpass 2 to 5 kHz, Hilbert envelope, composite score
  (silence before, tonal purity, duration), adaptive rise-foot edge,
  calibrated confidence with an auto-trust threshold of 0.95.
- **Candidates (voter A)**: ``librosa.onset.onset_detect`` spectral flux
  at about 10.7 ms frames, 80 ms minimum gap, 150 ms echo refractory,
  then a half-rise leading edge inside a 30 ms peak window. Gated at the
  lowest positive confidence in the calibration set.
- **Voter B**: LAION ``clap-htsat-unfused`` zero-shot, 1 s window at
  48 kHz, difference between four shot prompts and six distractor
  prompts.
- **Voter C**: ``GradientBoostingClassifier`` per camera class over 31
  columns: 17 hand-crafted (peak, RMS ratios, attack, tail, multi-band
  ratios, AGC state, spectral flatness and peak ratio, test-time
  augmentation agreement), 10 CLAP prompt similarities, the CLAP
  differential, PANN CNN14 ``Gunshot, gunfire`` probability, and a
  camera-class one-hot. Adaptive top-(K + slack) mode when the expected
  round count is known, with a 0.60 confidence override. Shipped as one
  ONNX graph per camera class.
- **Voter E** (visual, CLIP ViT-B/32 frame embedding plus a linear
  probe): implemented, disabled in the shipped calibration. The last
  build recorded zero visual candidates and 21 fixtures skipped for
  missing video, so the probe has never been trained on the corpus.
- **Consensus**: voter C required, plus one of A or B. The 2026-05-11
  sweeps showed A and B thresholds have no leverage under that rule.
- **Audio path is mono.** ``ui/audio.py`` extracts with ``-ac 1``; any
  stereo cue in the source recording is discarded before detection.
- **Reported quality** (30 fixtures, 576 positives, 75 ms tolerance):
  F1 0.971, precision 0.958, recall 0.984. Headcam precision 0.937 (16 FP
  on 254 kept) is the weak cell; handheld 0.973. The residual false
  positives are cross-bay shots and echoes on the noisier stages
  (stage 3 of the blacksmith match, 4 FP on 14 kept).
- **Runtime**: onnxruntime only in the wheel. PANN ONNX is 327 MB, the
  CLAP audio encoder is downloaded on first use.

### 2.1 The evaluation leak

``scripts/build_ensemble_artifacts.py`` fits voter C on every
calibration candidate of a camera class and picks its threshold from
``StratifiedKFold(n_splits=5, shuffle=True)`` over candidates. Two
consequences:

- Candidates from one stage are spread over all five folds, so the
  held-out probabilities see near-duplicates of the same acoustic
  context. The same is true across cameras: ``stage5-s97dcec94`` and
  ``stage5-s97dcec94-apple-iphone17pro`` are the same 24 shots.
- ``latest_report.md`` replays the shipped model over the corpus it was
  fitted on. The 0.971 is a training-set figure for voter C and an
  honest figure only for the A and B thresholds.

Neither invalidates the shipped detector (the audit UI is the safety
net), but it means a candidate encoder cannot be compared against the
current one until the split is grouped by (event, stage) with all
cameras of a stage in the same fold, or a whole match is held out.
This is a prerequisite for everything in section 6.

## 3. The market

| Product | Method as published | Claims | Pricing | Notes |
|---|---|---|---|---|
| Shot Streamer (web) | "machine learning model" over uploaded video; sensitivity slider 0.1 to 0.9; waveform editor; dual-angle merge | "95%+ accuracy on most competition shooting videos", "trained on thousands of shots from USPSA, IPSC and Steel Challenge" | Free tier; Pro adds scoring overlays, saved projects, no watermark | Site did not resolve on 2026-09-17 (DNS); figures are from search snippets of shotstreamer.com |
| Shooting Cut (iOS, ShootingCut LLC) | On-device audio analysis ("tap Analyze to find the timer and every shot on a live waveform"), tunable sensitivity, manual correction | none | $4.99/week, $9.99/month, $59.99/year; free with watermark | v1.0 April 2025, v1.1.9 August 2025; imports PractiScore, ESS, HDP, Shoot'n Score It scores |
| Shotisize AI (Shooters Global Drills app, iOS/Android) | "detects the timer start signal in a video and time codes of all shots and splits"; works with any timer and camera | none | App free, some features subscription | Recognises PAR times; overlay only |
| IPSC Shot Timer (iOS/Android) | Phone as a shot timer; video review with manual shot editor | none | app store | Not a video analyser; closest to the "Splits" phone-timer class |

Assessment: the category splitsmith occupied alone in 2025 now has three
consumer entrants. All three are overlay and highlight tools first;
none exposes a confidence, an audit trail, a per-camera calibration, or
a published error rate on a fixed corpus. The Shot Streamer accuracy
claim is unqualified (no tolerance, no corpus, no definition of a shot
event). Splitsmith's ``docs/ensemble_dashboard`` and fixture corpus are
an asset a commercial version should keep public.

Adjacent but not comparable: SoundThinking (ShotSpotter), AmberBox and
similar public-safety gunfire detectors work on distant gunfire with
fixed sensor arrays and infrared; nothing transfers except the physics.

## 4. The research and model landscape since 2025

### 4.1 General audio encoders (feature sources for voter C)

AudioSet-2M mAP is the common yardstick. Higher is better; the current
pipeline's two learned inputs sit at the bottom of the table.

| Model | Year | Params | AS-2M mAP | Input | Licence | Notes |
|---|---|---|---|---|---|---|
| SSLAM (Surrey) | 2025 | 88M | 0.502 | 16 kHz | MIT | Trained on mixtures, +9.1% on polyphonic sets; the closest match to "my shot over a neighbour's shot" |
| EAT (CAS) | 2024 | 88M / 309M | 0.486 to 0.495 | 16 kHz | MIT | Fast pre-training; HF checkpoints |
| Dasheng (Xiaomi) | 2024 | 86M / 0.6B / 1.2B | 0.497 fine-tuned base | 16 kHz | Apache 2.0 | HEAR environment score 80 to 83; feeds MiDashengLM |
| BEATs iter3 (Microsoft) | 2023 | 90M | 0.480 | 16 kHz | MIT | Still the DCASE SED workhorse (frozen embeddings into a CRNN) |
| OpenBEATs (CMU/ESPnet) | 2025 | base and large | see paper | 16 kHz | CC BY 4.0 (model cards) | Full pre-training code; multi-domain data |
| M2D-CLAP 2025 (NTT) | 2025 | 86M | 0.490 | 16 kHz | NTT evaluation licence, non-commercial | Excluded for anything shipped |
| BAT (Surrey) | 2026 | 91M | 0.485 | 16 kHz | not checked | Convex gated probing narrows the fine-tune vs probe gap; relevant if we only train a head |
| CED (Xiaomi) | 2023 | tiny to base | up to 0.50 | 16 kHz | Apache 2.0 | Distilled; the tiny variant is a few MB |
| EfficientAT MN | 2023 | 30M | 0.476 | 32 kHz | MIT | CNN, cheapest per inference |
| PANN CNN14 (in use) | 2020 | 81M | 0.431 | 32 kHz | MIT | 327 MB ONNX shipped |
| LAION CLAP-HTSAT (in use) | 2023 | 86M | 0.463 | 48 kHz | Code CC0 1.0; weights carry no separate licence | Training data withheld "due to copyright reasons" |

Two things matter beyond the number. First, the SSL encoders take
16 kHz; the hand-crafted 8 kHz-and-up band ratio stays on the 48 kHz
path, so a swap loses nothing there. Second, all of these are clip
classifiers with patch-level time resolution of roughly 100 to 160 ms;
they are candidate *classifiers*, never timers. That division of labour
is exactly what the pipeline already has.

The ICME 2025 audio encoder challenge submission from CMU and AIST found
an ensemble of Dasheng-1.2B with scaled BEATs models beat either alone,
and a DCASE 2025 report noted Dasheng and EAT are complementary on
machine-sound tasks. For our purposes one encoder as a feature column is
the right first step; two is a later ablation.

### 4.2 Open-vocabulary, frame-level sound event detection

The new thing since 2025 is contrastive audio-language models that
localise in time rather than score a whole clip.

- **FLAM** (Adobe Research, Mila, MIT; ICML 2025). HTSAT backbone at
  48 kHz, 32 frames per 10 s (about 312 ms per frame), open-vocabulary
  activation maps for a text query. AUROC 81.2 on their ASFX-SED set
  versus 69.6 for the CLAP baseline. Code and weights (OpenFLAM) are
  under a **non-commercial Adobe Research licence**.
- **FlexSED** (JHU LCAP; WASPAA 2025, code and checkpoint October 2025,
  **MIT**). A pretrained SSL audio encoder plus the CLAP text encoder in
  an encoder-decoder with adaptive fusion; beats vanilla SED on
  AudioSet-Strong and does zero-shot and few-shot. This is the
  commercially usable one.
- Related: T-CLAP (temporal-enhanced CLAP), TACOS (temporally aligned
  captions), and the 2026 timestamped-captioning work (TAC). All still
  at 100 ms or coarser.

Why it matters here: cross-bay and echo false positives are decided by
*context* (what happened in the previous seconds, AGC state, event
density), and the current voters see a 1 s window plus a handful of
history scalars. A frame-level "gunshot" activation track over the whole
stage, sampled at each candidate, is a context feature the 1 s window
cannot give. It is a feature-column experiment, not a new voter.

### 4.3 Large audio-language models

Qwen3-Omni (Apache 2.0), MiDashengLM-7B and 0.6B (Apache 2.0), Kimi-Audio
(MIT and Apache), Step-Audio 2 mini (Apache 2.0), Audio Flamingo 3
(NVIDIA OneWay non-commercial), Gemini 2.5 and 3 (API terms).

TAG-Bench (2026) evaluated 21 systems on returning time intervals for a
natural-language query: best 31.2 mIoU, only one system above 20 on
longer audio, best recall at IoU >= 0.7 was 21.5%, and no system passed
13.2% count accuracy for multi-occurrence queries. A separate 2026 study
puts Gemini 2.5 Flash and Pro under 40 mIoU on every temporal grounding
benchmark, with Gemini 3 scoring lower than 2.5. Counting shots or
timing them with one of these is not viable and will not be soon.

What they can do is answer "is this 1 s clip the shooter's own pistol
or a neighbouring bay?" for the handful of candidates the voters
disagree on. Cost is trivial (Gemini bills audio at 32 tokens per second;
a 1 s window is under a tenth of a cent). Accuracy on that question is
unmeasured; section 6 lists it as an optional audit-side experiment, not
a pipeline stage.

### 4.4 Gunshot-specific research and datasets

The 2025 and 2026 gunshot papers are almost all "which spectrogram and
which CNN" studies on small datasets (a 2025 J. Imaging paper compares
twelve time-frequency representations on 2,148 shots; a 2026 arXiv paper
sweeps MFCC/LPC/GTCC parameters). None addresses body-worn recording,
AGC, or discriminating the wearer's own gun from a neighbour's. Nothing
to adopt, but the datasets are useful:

| Dataset | Size | Recording | Licence |
|---|---|---|---|
| Kabealo et al. 2023 (Florida Tech, Zenodo 7004819) | 2,148 shots, 4 firearms (AR-556, 870 shotgun, .38 revolver, Glock 17), edge devices at an outdoor range, 44.1 kHz mono | multi-orientation, time-synced devices | **CC BY 4.0** |
| C3GD, Certus Caliber Classification Gunshot Dataset (May 2026, Zenodo) | 8,000+ shots, 28 firearms, 16 calibres, Tascam DR-05XP, DJI Mic, Galaxy Tab, Pixel 7, resampled to 48 kHz; three outdoor field sites | rich metadata; authors note no indoor or urban reverberation | Paper CC BY 4.0; dataset licence to confirm on the Zenodo record |
| AudioSet temporally-strong labels | about 100k clips at 0.1 s resolution, has a ``Gunshot, gunfire`` class | YouTube audio, labels only | Labels CC BY 4.0; audio not redistributed |
| UrbanSound8K, ESC-50 | 8,732 and 2,000 clips | Freesound | **CC BY-NC** (both); do not train a commercial model on them |
| FSD50K | 51k clips | Freesound | per-clip CC0 / CC BY / CC BY-NC mix; filter by clip |

Their value to splitsmith is as **negatives and augmentation**: shots
from other guns at other distances are the "not this shooter" class the
headcam model struggles with, and mixing them at low level into
fixture audio is a principled way to manufacture cross-bay examples
whose ground truth is known. They cannot stand in for the fixture
corpus because none was recorded by a camera on a shooter.

### 4.5 The visual side

Voter E used CLIP ViT-B/32 image embeddings of a single frame. A shot is
a motion event (recoil, slide cycling, brass) and the strongest visual
discriminator between the wearer's shot and a neighbour's is that only
the wearer's gun moves. Options, cheapest first:

1. **Frame-difference or optical-flow energy** in a gun region of
   interest over a 200 ms window around the candidate. No model, no
   licence, runs on the existing ffmpeg frame grab. This is what to try
   first.
2. **Image encoders with a permissive licence** if a semantic probe is
   still wanted: SigLIP 2 (Apache 2.0), Meta Perception Encoder
   (Apache 2.0), DINOv3 (Meta's own commercial licence, redistribution
   must carry it). CLIP itself is MIT, so the current choice is not a
   licensing problem, only a modelling one.
3. **Video encoders**: V-JEPA 2 (MIT) gives motion-aware clip
   embeddings; heavier than the project needs at present.

Video frame rate (30 or 60 fps, 16 to 33 ms) is too coarse to time a
split, so vision stays a veto, never a timer. Muzzle flash is not a
usable cue in daylight footage.

None of this can be trained until the fixture corpus carries its source
videos: the last artifact build skipped 21 fixtures for missing video
and trained on zero visual candidates. The source paths in the fixture JSON point at
external volumes (``/Volumes/mathias``, ``/Volumes/X9``) that were not
mounted during this review, so the build machine needs them or a proxy
copy in the corpus.

### 4.6 Timing

Nothing since 2025 changes the timing story. Neural onset detectors
(CNN, RNN and TCN designs from the music information retrieval
literature) work at 10 ms frames and target musical notes; for an
impulsive event with a 1 to 2 ms rise, the half-rise definition already
in ``shot_detect.py`` is as good as the sample rate allows and is
self-consistent across cameras and gain. Keep it. Do not put any
learned denoiser (DeepFilterNet, MIT or Apache 2.0, is the usual pick for
wind) on the timing path: denoisers reshape transients. If wind is ever
a classification problem, denoise a copy for the feature extractor only.

### 4.7 Stereo

The source recordings may carry a spatial cue the pipeline throws away.
An iPhone 17 Pro records stereo video; the Oakley Meta Vanguard has a
five-microphone array (channel count of its files unverified); the
Insta360 GO 3S records mono according to its manual. A neighbouring
bay's shot arrives from a different bearing than the wearer's own
muzzle, so the inter-channel level and time difference at the onset is
a physical cross-bay discriminator for any stereo source. This is one
``ffprobe`` on each camera's files and two feature columns. Untested,
listed because it is cheap.

## 5. Licensing summary for a commercial splitsmith

Splitsmith itself is MIT. The question is what it may embed.

**Usable commercially (permissive licence on code and weights):**
EAT (MIT), SSLAM (MIT), BEATs (MIT), CED (Apache 2.0), Dasheng and
MiDashengLM (Apache 2.0), OpenBEATs (CC BY 4.0, attribution), EfficientAT
(MIT), PaSST (Apache 2.0), HTS-AT (MIT), FlexSED (MIT), PANN (MIT),
OpenAI CLIP (MIT), SigLIP 2 (Apache 2.0), Perception Encoder
(Apache 2.0), V-JEPA 2 (MIT), Qwen3-Omni (Apache 2.0), Kimi-Audio
(MIT/Apache 2.0), Step-Audio 2 mini (Apache 2.0), DeepFilterNet
(MIT or Apache 2.0), DINOv3 (Meta commercial licence with
redistribution conditions), onnxruntime (MIT), librosa (ISC).

**Not usable in anything shipped:** M2D and M2D-CLAP (NTT "Software
License Agreement for Evaluation": internal, non-commercial evaluation of
the paper's methods only, no redistribution), FLAM / OpenFLAM (Adobe
Research non-commercial), Audio Flamingo 3 (NVIDIA OneWay
non-commercial).

**Ambiguous, resolve before relying on it:**

- LAION CLAP: the repository is CC0 1.0 but the checkpoint files carry
  no separate licence statement and the training set is withheld for
  copyright reasons. Practical exposure is low and shared with everyone
  using it, but it is the one component in the shipped stack without a
  clean weights licence. Replacing it with an MIT encoder removes the
  question.
- Microsoft CLAP 2023: GitHub says MIT, the Hugging Face card says
  MS-PL. Both permit commercial use; MS-PL is not GPL-compatible, which
  does not matter for an MIT project.
- Any model trained on AudioSet (which is all of them) inherits the
  unsettled question of weights derived from YouTube audio. The weights
  licence is what a court or a customer's counsel will look at first;
  no encoder in the permissive list is worse off than PANN, which
  already ships.

**Datasets:** Kabealo 2023 and the C3GD paper are CC BY 4.0 (attribute
in the artifact provenance). ESC-50 and UrbanSound8K are CC BY-NC and
must stay out of any training set that feeds a shipped model. FSD50K
needs per-clip filtering. AudioSet strong labels are CC BY 4.0.

**The fixture corpus:** it is the project's own recordings and the most
valuable asset here. It contains bystanders' speech and other
competitors' shots; publishing it as an open benchmark is a separate
decision from using it to train.

## 6. Recommendations, ranked

Each item names the expected effect, the cost, and the check that
proves it. Items 1 and 2 are prerequisites; the rest are independent and
each is one feature-column experiment on the existing dashboard.

1. **Fix the evaluation split** (prerequisite). Group folds by
   (event, stage) with every camera of a stage in one fold, or hold out
   one match. Re-run ``build_ensemble_artifacts.py`` and
   ``run_sweep.py``, and record the honest baseline next to the
   in-sample 0.971. Half a day. Without this, nothing below can be
   measured.
2. **Get the source videos back into the corpus build** (prerequisite
   for anything visual). Either mount the volumes on the build machine
   or add 480p proxies of the fixture windows to the corpus.
3. **Swap the learned audio features for one modern encoder.** Add an
   SSLAM or EAT-base embedding (MIT, 16 kHz, one ONNX graph) of the 1 s
   candidate window as voter C columns; retrain; compare on the grouped
   split. Then ablate PANN out. Expected: the headcam precision cell is
   where the 25 FPs live and both encoders are trained on polyphonic
   mixtures. Net artifact size is neutral if PANN goes (327 MB out,
   about 350 MB in). Two to three days including the ONNX export and
   parity test the repo already has patterns for.
4. **Add a context track.** Run FlexSED (MIT) with the query "gunshot"
   over the whole stage once, sample its activation at each candidate
   and at a few offsets before it, and add those as columns. This is
   the one open-vocabulary SED model with a usable licence. One to two
   days after item 3's plumbing exists.
5. **Replace the CLIP probe with motion energy** on the headcam class
   only: frame-difference energy in a fixed lower-centre region over
   plus or minus 100 ms of the candidate, as a voter E veto. No new
   dependency. Needs item 2.
6. **Grow the corpus before growing the model.** Target: five or more
   matches, four or more camera models, at least one indoor range, and
   a second shooter per match. Use Kabealo and C3GD shots mixed at low
   level into fixture audio as synthetic cross-bay negatives with known
   truth. Retrain and watch the per-camera table.
7. **Stereo probe.** ``ffprobe`` the channel count of each camera's
   files; where stereo, add onset-window inter-channel level and time
   difference as two columns. An afternoon.
8. **Optional: LLM second opinion in the audit UI.** For candidates
   where voter C and the consensus disagree, ask Qwen3-Omni locally or
   Gemini over the 1 s clip and show the answer beside the candidate.
   Never as a voter. Measure agreement with the human before making it
   visible by default.

**Do not:** use an audio-language model for shot timing or counting;
ship M2D, FLAM or Audio Flamingo 3; put a denoiser on the timing path;
replace half-rise with a learned onset; train on ESC-50 or UrbanSound8K.

## Sources

Products

- Shot Streamer: https://shotstreamer.com/ and
  https://shotstreamer.com/how-it-works.html (unreachable on
  2026-09-17; claims quoted from search snippets)
- Shooting Cut: https://apps.apple.com/us/app/shooting-cut/id6761160281
- Shotisize AI (Shooters Global Drills): https://timer.shooters.global/drills/
- IPSC Shot Timer: https://apps.apple.com/us/app/ipsc-shot-timer/id6766259993

Encoders and benchmarks

- CodeSOTA AudioSet leaderboard (March 2026): https://www.codesota.com/audio/classification
- SSLAM (ICLR 2025): https://arxiv.org/abs/2506.12222, https://github.com/ta012/SSLAM
- EAT (IJCAI 2024): https://github.com/cwx-worst-one/EAT
- Dasheng: https://github.com/XiaoMi/dasheng; MiDashengLM:
  https://github.com/xiaomi-research/dasheng-lm, https://arxiv.org/pdf/2508.03983
- BEATs: https://github.com/microsoft/unilm/tree/master/beats
- OpenBEATs: https://arxiv.org/abs/2507.14129,
  https://huggingface.co/espnet/OpenBEATS-Base-i1-fsd50k
- M2D: https://github.com/nttcslab/m2d (LICENSE.pdf)
- BAT (2026): https://arxiv.org/abs/2602.16305
- CED: https://github.com/RicherMans/CED
- EfficientAT: https://github.com/fschmid56/EfficientAT
- PaSST: https://github.com/kkoutini/PaSST
- HTS-AT: https://github.com/RetroCirce/HTS-Audio-Transformer
- ICME 2025 audio encoder challenge, CMU-AIST: https://arxiv.org/pdf/2601.16273
- PANNs: https://github.com/qiuqiangkong/audioset_tagging_cnn
- LAION CLAP: https://github.com/LAION-AI/CLAP,
  https://huggingface.co/laion/clap-htsat-unfused
- Microsoft CLAP: https://github.com/microsoft/CLAP,
  https://huggingface.co/microsoft/msclap

Open-vocabulary and frame-level SED

- FLAM (ICML 2025): https://arxiv.org/abs/2505.05335,
  https://github.com/adobe-research/openflam
- FlexSED (WASPAA 2025): https://arxiv.org/abs/2509.18606,
  https://github.com/JHU-LCAP/FlexSED
- T-CLAP: https://arxiv.org/html/2404.17806v1
- TACOS: https://arxiv.org/html/2505.07609v1
- TAC, timestamped audio captioning (2026): https://arxiv.org/pdf/2602.15766
- AudioSet strong labels: https://arxiv.org/pdf/2105.07031,
  https://dcase-repo.github.io/dcase_datalist/datasets/sounds/audioset_temporal.html

Audio-language models and temporal grounding

- TAG-Bench (2026): https://arxiv.org/html/2609.01542
- SpotSound (2026): https://arxiv.org/html/2604.13023v1
- Tempo (2026): https://arxiv.org/html/2608.29999v1
- Qwen3-Omni: https://arxiv.org/abs/2509.17765,
  https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct
- Audio Flamingo 3: https://huggingface.co/nvidia/audio-flamingo-3
- Kimi-Audio: https://github.com/MoonshotAI/Kimi-Audio
- Step-Audio 2: https://github.com/stepfun-ai/Step-Audio2
- Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing

Gunshot research and datasets

- Kabealo et al., Data in Brief 2023: https://pmc.ncbi.nlm.nih.gov/articles/PMC10114508/,
  dataset https://zenodo.org/records/7004819
- C3GD (2026): https://arxiv.org/abs/2606.18135
- Spectrogram comparison study (2025): https://pmc.ncbi.nlm.nih.gov/articles/PMC12387842/
- Feature extraction parameter study (2026): https://arxiv.org/abs/2606.19568
- ESC-50 / UrbanSound8K / FSD50K licence notes: https://arxiv.org/pdf/2606.05571,
  https://dl.acm.org/doi/10.1109/TASLP.2021.3133208

Vision and preprocessing

- SigLIP 2: https://github.com/google-research/big_vision/blob/main/big_vision/configs/proj/image_text/README_siglip2.md
- DINOv3 licence: https://ai.meta.com/resources/models-and-libraries/dinov3-license/
- V-JEPA 2: https://github.com/facebookresearch/vjepa2
- Perception Encoder: https://huggingface.co/facebook/PE-Core-B16-224
- DeepFilterNet: https://github.com/Rikorose/DeepFilterNet
- Insta360 GO 3S audio modes: https://onlinemanual.insta360.com/go3s/en-us/operating_tutorials/capture-preview/shooting-parameter/audio-mode-settings
- Oakley Meta Vanguard: https://about.fb.com/news/2025/09/oakley-meta-vanguard-performance-ai-glasses-sports/
