# Changelog

## [0.2.0](https://github.com/mandakan/splitsmith/compare/v0.1.0...v0.2.0) (2026-05-24)


### Features

* **audit:** close design gaps -- confidence-aware beep chip, prereq gate, quiet K toggle ([db35feb](https://github.com/mandakan/splitsmith/commit/db35febee5c9dcd7ecf591bf70eaaaba9d5e409d))
* **audit:** conveyor redesign -- waveform-led layout + sticky action bar ([#359](https://github.com/mandakan/splitsmith/issues/359)) ([429a849](https://github.com/mandakan/splitsmith/commit/429a8494baf4bf371e18acaa000b6d25ff9d7f3f))
* **audit:** per-shooter interstitial done state on conveyor handoff ([fd23666](https://github.com/mandakan/splitsmith/commit/fd236665173a40400577e48a583e9e81b2bfcf24))
* **audit:** shooter switcher chip strip for multi-shooter matches ([ca65818](https://github.com/mandakan/splitsmith/commit/ca65818a9665733754dd726fc8ae4a35fd050699))
* **match:** stable match_id field + MatchRegistry ([#353](https://github.com/mandakan/splitsmith/issues/353) Phase 3 PR A) ([#365](https://github.com/mandakan/splitsmith/issues/365)) ([58b679a](https://github.com/mandakan/splitsmith/commit/58b679a717bc832c7d3760cf32d9666842af7153))
* merge legacy projects from the SPA ([#332](https://github.com/mandakan/splitsmith/issues/332)) ([f926558](https://github.com/mandakan/splitsmith/commit/f926558e8404c271e3db5dbee018b8667ce484f0))
* **models:** slim runtime model layer + fetch-models CLI + /api/models/status ([#379](https://github.com/mandakan/splitsmith/issues/379)) ([1cb9fd7](https://github.com/mandakan/splitsmith/commit/1cb9fd78322efb09f66d10f12c017b769714438a))
* **overlay:** bundle Antonio + JetBrains Mono so splitsmith theme renders deterministic typography ([6126983](https://github.com/mandakan/splitsmith/commit/61269839f22f82530932e7567d66dd2433afcfd0))
* **overlay:** design-system theme as default, 'clean' as the neutral preset ([33fccbb](https://github.com/mandakan/splitsmith/commit/33fccbb394a9de9d37643dd07b8f94d028924997))
* **packaging:** bundled-binary discovery for ffmpeg/ffprobe ([#375](https://github.com/mandakan/splitsmith/issues/375)) ([d7a8570](https://github.com/mandakan/splitsmith/commit/d7a8570b678f0e1e246d6192352bcbedf1bcc691))
* **packaging:** graceful shutdown endpoint for embedded sidecar ([#374](https://github.com/mandakan/splitsmith/issues/374)) ([65e40c3](https://github.com/mandakan/splitsmith/commit/65e40c3ed1a16ab4dce186da741930dabc43aede))
* **packaging:** rotating file logging for embedded sidecar ([#372](https://github.com/mandakan/splitsmith/issues/372)) ([8a1b313](https://github.com/mandakan/splitsmith/commit/8a1b3130d959ac2b875403cf77bb5b313c717af8))
* **routing:** URL-scoped shooter binding -- phase 1 of [#353](https://github.com/mandakan/splitsmith/issues/353) ([76c8861](https://github.com/mandakan/splitsmith/commit/76c886150c0d79078598316bbf258f25256636d5))
* **runtime:** process-wide config for artifacts + binaries ([#130](https://github.com/mandakan/splitsmith/issues/130)) ([#363](https://github.com/mandakan/splitsmith/issues/363)) ([a5ab724](https://github.com/mandakan/splitsmith/commit/a5ab724c9c730f8b45834f3fbe8c1c5d2d94760d))
* **shell:** activeMeaning kicker on shooter strip, drop per-page duplicates ([061c077](https://github.com/mandakan/splitsmith/commit/061c0776f083fb559c558a9d402c550d86ac8bcc))
* **shooters:** rebuild missing trim caches per shooter from the UI ([#351](https://github.com/mandakan/splitsmith/issues/351)) ([de9cc1f](https://github.com/mandakan/splitsmith/commit/de9cc1fd30df7b6e5f2007d7ba0ed7abcfe0ede1))
* **slim:** CLAP audio trunk ONNX export + pre-baked text embeddings ([#382](https://github.com/mandakan/splitsmith/issues/382)) ([8f0c60e](https://github.com/mandakan/splitsmith/commit/8f0c60ed454f8d6e8245513a4ec0921fb70161da))
* **slim:** CLAP ONNX runtime branch + license-clean numpy mel-spectrogram ([#383](https://github.com/mandakan/splitsmith/issues/383)) ([351debb](https://github.com/mandakan/splitsmith/commit/351debb2f9f37c640a8c7345b3979fdf021f76a0))
* **slim:** first-launch ffmpeg / ffprobe presence check with 24h cache ([#380](https://github.com/mandakan/splitsmith/issues/380)) ([1e3641a](https://github.com/mandakan/splitsmith/commit/1e3641ad1c97c0fc5a2fcd4f807c3e2af19a7f7a))
* **slim:** maintainer upload helper + README mention of slim runtime ([#385](https://github.com/mandakan/splitsmith/issues/385)) ([6294e42](https://github.com/mandakan/splitsmith/commit/6294e42947fc2896fed41e261354f17dfd32d3b5))
* **slim:** move heavy ML deps to [dev], promote onnxruntime to runtime ([#384](https://github.com/mandakan/splitsmith/issues/384)) ([4e185c9](https://github.com/mandakan/splitsmith/commit/4e185c916af3c8768cf39ffcf5ab1cf2d4a26fd7))
* **slim:** PANN ONNX export + onnxruntime backend branch (first voter migrated) ([#381](https://github.com/mandakan/splitsmith/issues/381)) ([6815aaf](https://github.com/mandakan/splitsmith/commit/6815aafb3fcfb440bbf87bef7c5eb797b80c3999))
* **slim:** R2 publish + boto3 S3 multipart + calibration model_artifacts ([#386](https://github.com/mandakan/splitsmith/issues/386)) ([4dcd1bb](https://github.com/mandakan/splitsmith/commit/4dcd1bb77b421437255c26844ccee90876ed91f0))
* **ui:** /match/:matchId URL prefix + API alias ([#353](https://github.com/mandakan/splitsmith/issues/353) Phase 3 PR B) ([#366](https://github.com/mandakan/splitsmith/issues/366)) ([d93ed8f](https://github.com/mandakan/splitsmith/commit/d93ed8fb0354fbc38c8d20393904bf7f670263b5))
* **ui:** drop match singleton + canonical /match/:matchId URLs ([#353](https://github.com/mandakan/splitsmith/issues/353) Phase 3 PR C) ([#367](https://github.com/mandakan/splitsmith/issues/367)) ([36de212](https://github.com/mandakan/splitsmith/commit/36de212aae58c6d528dd4efa542352d01e497e78))
* **ui:** embeddable FastAPI entrypoint with structured handshake ([#131](https://github.com/mandakan/splitsmith/issues/131)) ([#364](https://github.com/mandakan/splitsmith/issues/364)) ([b713ac5](https://github.com/mandakan/splitsmith/commit/b713ac5beefaa32e5d75ea19d30f8590b00c2d9b))
* **ui:** post-b3531b5 design review + v2 audit chrome ([#362](https://github.com/mandakan/splitsmith/issues/362)) ([da57e77](https://github.com/mandakan/splitsmith/commit/da57e777aafc387ac0fd6486a68defbb098ae7fd))
* **ui:** UX overhaul -- stage status truth, ingest wizard, sync mode ([b3531b5](https://github.com/mandakan/splitsmith/commit/b3531b51bdec4d88a49794c36753a895e3e73aa5))


### Bug Fixes

* **ci:** wrap 102-char resolved_trim ternary so ruff stops failing ([4218a4a](https://github.com/mandakan/splitsmith/commit/4218a4a0d5963040c3353f0041298336833b3721))
* coach per-stage view auto-follows the playhead ([1f71920](https://github.com/mandakan/splitsmith/commit/1f71920667b49d7dfc2b512a0c9c7706885c8a73))
* CreateMatch returns to /pick via replace, not push ([55e4e7d](https://github.com/mandakan/splitsmith/commit/55e4e7d87f7c62c5363a53b628806484bd7572f6))
* **export:** surface source-offline state + drawer z-index + contrast ([#357](https://github.com/mandakan/splitsmith/issues/357)) ([345efb5](https://github.com/mandakan/splitsmith/commit/345efb590e4c2df783e8b4e6e4d2c50c1bac382f))
* filled-LED button option D actually wins the cascade ([e769084](https://github.com/mandakan/splitsmith/commit/e7690845aa13d22bf2260204bc48be45fcbb7dab))
* history-stack hygiene across shells + breadcrumbs ([d267702](https://github.com/mandakan/splitsmith/commit/d267702b05101f58222e327cd11d49a27ea9db06))
* **home:** drop misleading YOU ring on shooter cards ([#350](https://github.com/mandakan/splitsmith/issues/350)) ([badc563](https://github.com/mandakan/splitsmith/commit/badc563008c427da73f2e11b169a6f8060186f33))
* **home:** match overview renders all shooters, not just the bound one ([e361112](https://github.com/mandakan/splitsmith/commit/e3611123d5f0b24bedc5f9089dd22f2c3b555bf3))
* marker glyphs read --color-marker-* token (was --marker-*) ([2531b5e](https://github.com/mandakan/splitsmith/commit/2531b5e6b63edbb5306070f3bb12a292ade83f00))
* **match:** post-merge polish -- picker, compare playback, stub data ([44d3702](https://github.com/mandakan/splitsmith/commit/44d37023c2262bd9437920798211aeed5e8e7916))
* **overlay:** chain joinpath() for MultiplexedPath compatibility ([c907b7b](https://github.com/mandakan/splitsmith/commit/c907b7ba81cf9f93396146d12f4efefd4b168135))
* **portability:** pre-release cross-platform audit fixes ([#392](https://github.com/mandakan/splitsmith/issues/392)) ([6e7b528](https://github.com/mandakan/splitsmith/commit/6e7b5288d96bd73e3fc78980b1ca859addb4d719))
* remove per-shot cross-shooter comparisons in wf14 ([8f44271](https://github.com/mandakan/splitsmith/commit/8f44271feb5df8c3bb1e88af287118ea1ffb10fe))
* **slim:** pin Pillow as a runtime dep ([#388](https://github.com/mandakan/splitsmith/issues/388)) ([2e6ea5e](https://github.com/mandakan/splitsmith/commit/2e6ea5eee0d5b3247db4000036244d5366f51ad1))
* stale token refs across the SPA (rename rot) ([113ba1c](https://github.com/mandakan/splitsmith/commit/113ba1c3896f26463af9fb1ce490f20673c94db0))
* waveform reads canonical --color-waveform-* tokens ([72a41a9](https://github.com/mandakan/splitsmith/commit/72a41a96bc5357ff0cbb35636ce2c71d307a7762))
* widen role-toggles column in wf05 to prevent overlap ([fedd290](https://github.com/mandakan/splitsmith/commit/fedd290bbc37cb8ad4ce665bbfe46823954fa284))


### Refactors

* **ensemble:** backend selector + typed runtime callables (slim foundation) ([#378](https://github.com/mandakan/splitsmith/issues/378)) ([7cc4a46](https://github.com/mandakan/splitsmith/commit/7cc4a46b143c69a5139360130fcdfdc953363b23))


### Documentation

* accessibility principles (WCAG 2.2 AA baseline) ([3beaf2f](https://github.com/mandakan/splitsmith/commit/3beaf2f1edcf3f2b391144ab8d5f244a9e9645da))
* capability inventory + IA updates (multi-camera, coach split, ingest) ([91ea4ff](https://github.com/mandakan/splitsmith/commit/91ea4ff72fa4807bc6b9f41322486ec30b79f9e1))
* **corpus:** surface "detection keeps getting better" on site + README ([823c962](https://github.com/mandakan/splitsmith/commit/823c962e283c4ec076c6a62ddbd1f1f9f47875c3))
* design system plan (tokens + primitives + migration) ([0bc9687](https://github.com/mandakan/splitsmith/commit/0bc9687e3fe9a40141bda340dc40e16cbbd6aed2))
* drop false "can't carry a timer" claim, reframe around splits + clip prep ([#338](https://github.com/mandakan/splitsmith/issues/338)) ([a915b1e](https://github.com/mandakan/splitsmith/commit/a915b1e6c4135d08332c6a5d41218b5872d96046))
* information architecture for UX redesign ([db16cb0](https://github.com/mandakan/splitsmith/commit/db16cb00a1ed03950dd9f4e94acc7df322bcd578))
* ingest -- reference vs copy storage choice ([7af10ed](https://github.com/mandakan/splitsmith/commit/7af10ed39c2820da5b4361fa71f8af0647002bd7))
* jobs-to-be-done source material for UX redesign ([9d521d8](https://github.com/mandakan/splitsmith/commit/9d521d844a0d0504a2866141e83dda4d84567919))
* **local-slim:** plan to shrink the install via ONNX + first-run model fetch ([122e42c](https://github.com/mandakan/splitsmith/commit/122e42c30d7ef409843997b1a632958156bda029))
* MCP / skill / beep-detection coverage in README + SPEC ([#275](https://github.com/mandakan/splitsmith/issues/275)) ([9227751](https://github.com/mandakan/splitsmith/commit/92277515489e9ef17fca9bba08fb02a221989b32))
* onboarding journey doc + wireframe 04 create-match ([57a0d11](https://github.com/mandakan/splitsmith/commit/57a0d11cc7a6c02b77525643aa8d719d442703dc))
* polished match picker -- "Chronograph Quarterly" direction ([ffdcfbe](https://github.com/mandakan/splitsmith/commit/ffdcfbe25e8b60920c9ac25c2a6233c3e971ba0e))
* **polished:** rewrite match picker -- "Shot Timer" direction ([4bc611d](https://github.com/mandakan/splitsmith/commit/4bc611d8bb45050d7522504a09e3ca0ab560760b))
* **polished:** wf02 match overview -- mission-briefing dashboard ([a3cf99d](https://github.com/mandakan/splitsmith/commit/a3cf99d8dcc59c559f244ef5ea2488d27faf8295))
* **polished:** wf03 stage audit -- oscilloscope-grade audit surface ([3292dc9](https://github.com/mandakan/splitsmith/commit/3292dc9ea172ccc0fb49b94f7fa2feb065d625fe))
* **polished:** wf07 stage compare -- F1 telemetry sync timeline ([87b6f6e](https://github.com/mandakan/splitsmith/commit/87b6f6ecda05a707a949a95f8447dfa8266a309a))
* README install instructions for Linux + Windows ([#82](https://github.com/mandakan/splitsmith/issues/82)) ([71ef6b7](https://github.com/mandakan/splitsmith/commit/71ef6b7a6ed1c2622223ea5c9541f80d1f581920))
* README pnpm/Node prerequisites + SPA build in Install section ([#107](https://github.com/mandakan/splitsmith/issues/107)) ([7dfbc58](https://github.com/mandakan/splitsmith/commit/7dfbc5888211866ff0d93e9669da6bd0d6f58309))
* **readme:** restructure install around slim wheel + from-source paths ([#387](https://github.com/mandakan/splitsmith/issues/387)) ([829b740](https://github.com/mandakan/splitsmith/commit/829b740d3297d8f1228332385bb0fefbe6b0ccc8))
* redesign progress + per-shooter identity tokens ([5d4af71](https://github.com/mandakan/splitsmith/commit/5d4af7148216b3ba271e22abf9ef9bf30981ae24))
* restructure README around screenshot gallery; extract reference docs ([#333](https://github.com/mandakan/splitsmith/issues/333)) ([70fc990](https://github.com/mandakan/splitsmith/commit/70fc9908192081ec8d88d59108d0d1bf2413479f))
* resync redesign docs with shipped implementation + recent polish ([b3fbc17](https://github.com/mandakan/splitsmith/commit/b3fbc1735c6b817d9cd9e4a3310a6d27505ae36a))
* **saas:** SaaS readiness doc set (00-09 + README) ([#334](https://github.com/mandakan/splitsmith/issues/334)) ([5cdf37a](https://github.com/mandakan/splitsmith/commit/5cdf37aee8b8b8d6129a52c31a5cc57b03e5d421))
* **site:** drop headcam framing — any camera with audio works ([#361](https://github.com/mandakan/splitsmith/issues/361)) ([da157cd](https://github.com/mandakan/splitsmith/commit/da157cdc568fc8896a299d13711f3ebc49c3b7b9))
* SPEC + CLAUDE.md cover the compare package ([#274](https://github.com/mandakan/splitsmith/issues/274)) ([d2b5a6d](https://github.com/mandakan/splitsmith/commit/d2b5a6dfecafc2b4fe24ac9bcfe11076a7daa074))
* wireframe 01 -- shell + match picker ([082fd79](https://github.com/mandakan/splitsmith/commit/082fd79e85885c945d5aea4a959540bd2a3c8c62))
* wireframe 02 -- match overview ([20130c7](https://github.com/mandakan/splitsmith/commit/20130c7d11d887932a208dd7a5e1ab64b7ca0f8f))
* wireframe 03 -- stage audit (single shooter, multi-camera) ([9f56af6](https://github.com/mandakan/splitsmith/commit/9f56af6643c7266e4c198c659fd9b8e67126377d))
* wireframe 07 -- stage compare (multi-shooter sync) ([86822a0](https://github.com/mandakan/splitsmith/commit/86822a04184aa719d9783490cd6025bc43e52568))
* wireframe 08 -- match export configurator ([4ad9858](https://github.com/mandakan/splitsmith/commit/4ad9858815c872fd250c4e7bc08ffa1759017ba7))
* wireframe 09 -- developer mode corpus ([3411cdf](https://github.com/mandakan/splitsmith/commit/3411cdf5deb5c59f3ab6c60a885f5f82f5ace206))
* wireframe 10 -- developer mode review queue ([54ecac3](https://github.com/mandakan/splitsmith/commit/54ecac33cbbe7a991382ad2a93742fbcd8ee7548))
* wireframe 13 -- coach (match-wide) ([83cfbb4](https://github.com/mandakan/splitsmith/commit/83cfbb4bb253c6a03e188dfef9f170299be4ff41))
* wireframe 14 -- coach (per-stage) ([8c2b96d](https://github.com/mandakan/splitsmith/commit/8c2b96dfddf5e9e13f4cc1f7f166584bedbad018))
* wireframes 05 ingest + 06 batch beep review ([736778b](https://github.com/mandakan/splitsmith/commit/736778b93c3cbeddedc5e4e953fd8ccde02a532b))
* wireframes 11 validate + 12 retrain ([4b7b71f](https://github.com/mandakan/splitsmith/commit/4b7b71fb1831d82bea51cc06bf3e44fcfbbfa8ec))
* wireframes 15-18 -- jobs drawer, shooters, empty states ([aac0121](https://github.com/mandakan/splitsmith/commit/aac012197525318711594011ebfd7ea39ba1f7c9))


### Build / CI

* apply black to test_ui_server.py ([#117](https://github.com/mandakan/splitsmith/issues/117)) ([b6a3cdf](https://github.com/mandakan/splitsmith/commit/b6a3cdf037df7805898cb218d897b734816d18f1))
* fix ruff + black after redesign merges ([994e471](https://github.com/mandakan/splitsmith/commit/994e47185bd2df74801ae86438389add63dff3fe))
* fix ruff F401 in test_shot_refine and black formatting in cli ([27edb44](https://github.com/mandakan/splitsmith/commit/27edb44e457a2578b52b1f52d0ead485b304162d))
* fix ruff F401 in test_shot_refine and black formatting in cli ([be29fd7](https://github.com/mandakan/splitsmith/commit/be29fd7225601c2307f26d41d639b82799a775e7))
* **marketing:** drop pnpm `version:` from action-setup -- packageManager wins ([d4fbaec](https://github.com/mandakan/splitsmith/commit/d4fbaec65a67c71eea1f14fade9a022aa0ad6c02))
* **marketing:** install pnpm + Node before wrangler-action ([8409960](https://github.com/mandakan/splitsmith/commit/8409960c44cb626983b18a436c295fa9891ea1d9))
* **release:** wire release-please for PyPI publishing ([#390](https://github.com/mandakan/splitsmith/issues/390)) ([a9e04ab](https://github.com/mandakan/splitsmith/commit/a9e04ab3e1a638bf0c71b9751491e1629e56557b))
* **slim:** release-gating smoke job for the slim wheel ([#389](https://github.com/mandakan/splitsmith/issues/389)) ([41471fb](https://github.com/mandakan/splitsmith/commit/41471fb63c0e1199de09bfdd5f74883940ff8487))
