# Changelog

## [0.33.1](https://github.com/mandakan/splitsmith/compare/v0.33.0...v0.33.1) (2026-08-20)


### Bug Fixes

* **beep:** saturate silence-preference so loudness stops deciding the ranking ([#950](https://github.com/mandakan/splitsmith/issues/950)) ([2f9e7d2](https://github.com/mandakan/splitsmith/commit/2f9e7d26b24bfaaec12050edb210903511cb77b0))
* **ui:** point the scoreboard match link at /match, not the bare id path ([#954](https://github.com/mandakan/splitsmith/issues/954)) ([860d4b1](https://github.com/mandakan/splitsmith/commit/860d4b1f77e00a22607ff2eb8146d09f7793a72b))

## [0.33.0](https://github.com/mandakan/splitsmith/compare/v0.32.2...v0.33.0) (2026-08-18)


### Features

* **cleanup:** gate irreplaceable files on the CLI, and make the freed figure visible ([#925](https://github.com/mandakan/splitsmith/issues/925)) ([6b1c59c](https://github.com/mandakan/splitsmith/commit/6b1c59c88484611ada40ccb6bb38c89b4e691789))
* **cleanup:** reclaim space on hosted, and give the dialog a caller ([#921](https://github.com/mandakan/splitsmith/issues/921)) ([8d96819](https://github.com/mandakan/splitsmith/commit/8d968191b83ebc2f7ecb007ccbbd0a86ea4783dc))
* **dev:** eval the filtered subset from the Corpus toolbar ([#941](https://github.com/mandakan/splitsmith/issues/941)) ([a8e2742](https://github.com/mandakan/splitsmith/commit/a8e27423d044b6a1b4ddcd6701098b37c7a25fa4))
* **dev:** full-stage playback + one synced playhead on the workbench ([#943](https://github.com/mandakan/splitsmith/issues/943)) ([32df8c1](https://github.com/mandakan/splitsmith/commit/32df8c1a73e26517ff32c639935d92d2b37559f4))
* **dev:** honest post-build flow -- real numbers, no promote theater ([#946](https://github.com/mandakan/splitsmith/issues/946)) ([8ccd6c6](https://github.com/mandakan/splitsmith/commit/8ccd6c653ee02c52ff605774916791c88e0f0117))
* **dev:** review state and model membership on the corpus ([#936](https://github.com/mandakan/splitsmith/issues/936)) ([d853971](https://github.com/mandakan/splitsmith/commit/d853971cd24715b5d62ad2d1b59545530461ff16))
* **dev:** the labeling workbench belongs to Review, and Review can eval ([#942](https://github.com/mandakan/splitsmith/issues/942)) ([de4debd](https://github.com/mandakan/splitsmith/commit/de4debd4ffdbd5ee1da0bc0e1d1a1c5416ebb81c))
* **exports:** persistent export-run history ([#629](https://github.com/mandakan/splitsmith/issues/629)) ([bfe6ad4](https://github.com/mandakan/splitsmith/commit/bfe6ad4f17be8ffe4b80e5581d0d6cb202152e9c))
* **model:** ship ensemble v2026.08.17 with the 48-fixture HFO corpus ([#948](https://github.com/mandakan/splitsmith/issues/948)) ([f36fd75](https://github.com/mandakan/splitsmith/commit/f36fd75c5070967239807284612590bb5cbc36de))


### Bug Fixes

* **api:** gate the cleanup endpoint on the same opt-in the CLI and dialog use ([#927](https://github.com/mandakan/splitsmith/issues/927)) ([6618070](https://github.com/mandakan/splitsmith/commit/661807021d922b3c686191d72d84442725e64fb1)), closes [#926](https://github.com/mandakan/splitsmith/issues/926)
* **dev:** distinct FP colour in the lab outcome palette ([#937](https://github.com/mandakan/splitsmith/issues/937)) ([65a0bb6](https://github.com/mandakan/splitsmith/commit/65a0bb6c88309d5c837a684bb4e5384d0f11328d))
* **dev:** FN rows get the subclass vocabulary in the label dropdown ([#938](https://github.com/mandakan/splitsmith/issues/938)) ([c63c3b5](https://github.com/mandakan/splitsmith/commit/c63c3b5ef67d746215ad0ae19b9c0906116ab788))
* **dev:** one scroll surface per pane on the labeling page ([#939](https://github.com/mandakan/splitsmith/issues/939)) ([43875a5](https://github.com/mandakan/splitsmith/commit/43875a5faec35ad04fac1f128b63a22230abb602))
* **dev:** the labeling aside never scrolls ([#940](https://github.com/mandakan/splitsmith/issues/940)) ([33e88df](https://github.com/mandakan/splitsmith/commit/33e88df52f2490ef12a07e6d8d26c11b6f4ddc30))
* **dev:** wire Approve to corpus, and Retrain re-attaches to its job ([#944](https://github.com/mandakan/splitsmith/issues/944)) ([7ea6769](https://github.com/mandakan/splitsmith/commit/7ea67690075c143caa2bc017bc2f45a8454dd8a4))
* **jobs:** a SystemExit in a job body fails the job instead of hanging it ([#945](https://github.com/mandakan/splitsmith/issues/945)) ([9012d4c](https://github.com/mandakan/splitsmith/commit/9012d4c7a5f0fcc1ecad017094d8884309ef3f58))
* **lab:** route each fixture's camera class through the eval ensemble ([#947](https://github.com/mandakan/splitsmith/issues/947)) ([95d02a8](https://github.com/mandakan/splitsmith/commit/95d02a81b991d4f1845aff65477cd3875aeb6513))


### Refactors

* **sync:** delete the unreachable clause in the delete-corroboration ([#860](https://github.com/mandakan/splitsmith/issues/860)) ([110a680](https://github.com/mandakan/splitsmith/commit/110a6808735affb5e802c7fb99d01c899b6a8d08))

## [0.32.2](https://github.com/mandakan/splitsmith/compare/v0.32.1...v0.32.2) (2026-08-15)


### Performance

* **docker:** ship ffmpeg's shared build, not two copies of every codec ([#916](https://github.com/mandakan/splitsmith/issues/916)) ([48604b9](https://github.com/mandakan/splitsmith/commit/48604b937aac68ce1151a83894d81d8fb9f2fc64))
* **docker:** stop baking the 450 MB model cache into the image ([#918](https://github.com/mandakan/splitsmith/issues/918)) ([0607649](https://github.com/mandakan/splitsmith/commit/060764982fc46fe4bf4934b83bb63d412664b458))

## [0.32.1](https://github.com/mandakan/splitsmith/compare/v0.32.0...v0.32.1) (2026-08-15)


### Bug Fixes

* **docker:** pull ffmpeg from a pinned GitHub release, checksum it before extracting ([#914](https://github.com/mandakan/splitsmith/issues/914)) ([e1611b3](https://github.com/mandakan/splitsmith/commit/e1611b35da0511738cf511e51d215a8cc8ac1d62))

## [0.32.0](https://github.com/mandakan/splitsmith/compare/v0.31.0...v0.32.0) (2026-08-14)


### ⚠ BREAKING CHANGES

* **dev:** delete the legacy Lab page ([#897](https://github.com/mandakan/splitsmith/issues/897))

### Features

* **desktop:** refresh the linked account's display name ([#877](https://github.com/mandakan/splitsmith/issues/877)) ([bbc3f78](https://github.com/mandakan/splitsmith/commit/bbc3f781c788f06ade097fb31d14d9e1521bbe04))
* **dev:** delete the legacy Lab page ([#897](https://github.com/mandakan/splitsmith/issues/897)) ([cb5705c](https://github.com/mandakan/splitsmith/commit/cb5705cbf80ec8d727d972fb70e897fcdbdd9f97))
* **dev:** full-page fixture detail with labeling at /dev/corpus/:slug ([#893](https://github.com/mandakan/splitsmith/issues/893)) ([cbaae33](https://github.com/mandakan/splitsmith/commit/cbaae33b41c85b4fa1fe8168b14aee2344a5f094))
* **dev:** promotion on Corpus, tuning + sweeps on Validate ([#896](https://github.com/mandakan/splitsmith/issues/896)) ([9d6d591](https://github.com/mandakan/splitsmith/commit/9d6d5914bedae905555287c5d471d6cf78ac0c90))
* **dev:** review queue items deep-link to the fixture detail page for labeling ([#912](https://github.com/mandakan/splitsmith/issues/912)) ([bb8506d](https://github.com/mandakan/splitsmith/commit/bb8506d12ce0aa6ddeaa73bbbfe92cb3083f4914))
* **lab:** scoped eval merges into a same-config cached run ([#891](https://github.com/mandakan/splitsmith/issues/891)) ([1af97f8](https://github.com/mandakan/splitsmith/commit/1af97f8edae286902f15db16a5f88a2f8193ef46))
* **lab:** shooter selector in the batch-promote panel, all selected by default ([#886](https://github.com/mandakan/splitsmith/issues/886)) ([fac2759](https://github.com/mandakan/splitsmith/commit/fac2759cbf32c4f41a4b55aec1a614a2ac2fe466))


### Bug Fixes

* **agent-gpu:** force clean onnxruntime-gpu reinstall in the swap ([#880](https://github.com/mandakan/splitsmith/issues/880)) ([3bb889b](https://github.com/mandakan/splitsmith/commit/3bb889b68b63d5e91c7f773a60b4fb57060ca25a))
* **dev:** fixture-detail polish -- fetch-error state, filtered prev/next, accent nits ([#909](https://github.com/mandakan/splitsmith/issues/909)) ([39653e9](https://github.com/mandakan/splitsmith/commit/39653e9df68ee54e8fa79851f6304e362c8f1f01))
* **dev:** gate the fixture-detail auto-eval on hydration, not a 250ms timer ([#903](https://github.com/mandakan/splitsmith/issues/903)) ([d410028](https://github.com/mandakan/splitsmith/commit/d410028427e6054bd2f7754f06d6bfd62b056cf6))
* **dev:** one consensus control on Validate, ranged to the real 3-voter ensemble ([#910](https://github.com/mandakan/splitsmith/issues/910)) ([8012140](https://github.com/mandakan/splitsmith/commit/8012140b1bdbd5d6decd40e76fbeff8e254e1c6e))
* **lab:** explain the pre-eval state and offer a per-fixture eval ([#887](https://github.com/mandakan/splitsmith/issues/887)) ([5c64d18](https://github.com/mandakan/splitsmith/commit/5c64d187b439882bf7003b9f465b1aef7ad06fa9))
* **lab:** scroll the fixture detail drawer into view on row click ([#888](https://github.com/mandakan/splitsmith/issues/888)) ([2237ac5](https://github.com/mandakan/splitsmith/commit/2237ac581ec3959263fb5f97c55f81f6bb03b0f9))
* **lab:** separate the shooter slug from the fixture slug in promote ([#885](https://github.com/mandakan/splitsmith/issues/885)) ([6dec3e0](https://github.com/mandakan/splitsmith/commit/6dec3e07d9d362d926457ae6e87d804ae20f68ca))
* **ui:** give the dev-mode Lab an explicit match context ([#883](https://github.com/mandakan/splitsmith/issues/883)) ([b1be1c5](https://github.com/mandakan/splitsmith/commit/b1be1c5321e627dce09b1f03568d8e9a344126d7))
* **ui:** thread the chosen match through mode flips and dev-mode picks ([#884](https://github.com/mandakan/splitsmith/issues/884)) ([3d6d9d5](https://github.com/mandakan/splitsmith/commit/3d6d9d5f67eb276b79b8170389ce838086575ea5))


### Refactors

* **dev:** drop the legacy compat shims from the shared lab components ([#911](https://github.com/mandakan/splitsmith/issues/911)) ([9e23cfd](https://github.com/mandakan/splitsmith/commit/9e23cfd755376a9d01b94ea8e1713ca64b0a7d31))
* **lab:** extract lab primitives to components/lab ([#890](https://github.com/mandakan/splitsmith/issues/890)) ([81b5dd6](https://github.com/mandakan/splitsmith/commit/81b5dd6fce623b11f03741d7a5c9b7e4f2c8a90e))


### Build / CI

* no workflow applies a migration -- gate it ([#876](https://github.com/mandakan/splitsmith/issues/876)) ([89dd37b](https://github.com/mandakan/splitsmith/commit/89dd37bb7d0132201fa2ab70b3d2fdc39cdcc5fc))

## [0.31.0](https://github.com/mandakan/splitsmith/compare/v0.30.0...v0.31.0) (2026-08-14)


### Features

* pin camera streams to the measured clip kind ([#874](https://github.com/mandakan/splitsmith/issues/874)) ([0267be8](https://github.com/mandakan/splitsmith/commit/0267be8f725038324fa2b2cdba5bc0424b9ad507))
* **ui:** mobile audit screen (mobile audit design, step 6) ([6abcd80](https://github.com/mandakan/splitsmith/commit/6abcd80b8a2cb0078f29ab7f7b1e0b1acc586a91))


### Bug Fixes

* **ui:** video kind from peaks flag, dirty-guard on back, guarded 409 reload ([54f402d](https://github.com/mandakan/splitsmith/commit/54f402d2ccbf4db2c07bd36578cccc5a5562ac9e))


### Refactors

* **ui:** camera polish follow-ups from [#868](https://github.com/mandakan/splitsmith/issues/868) review ([#871](https://github.com/mandakan/splitsmith/issues/871)) ([9c9d62d](https://github.com/mandakan/splitsmith/commit/9c9d62d9249a7d9aaa915b27393136f86bf6e54c))

## [0.30.0](https://github.com/mandakan/splitsmith/compare/v0.29.0...v0.30.0) (2026-08-14)


### Features

* **account:** a display name accounts can set, and codes that tell commenters apart ([#869](https://github.com/mandakan/splitsmith/issues/869)) ([4651b01](https://github.com/mandakan/splitsmith/commit/4651b01c067396c24f842a60ab15094f5f0e1560))
* **share:** timestamped comments on shared stage video ([#866](https://github.com/mandakan/splitsmith/issues/866)) ([1a11a22](https://github.com/mandakan/splitsmith/commit/1a11a22bf574b9791ddaea872176949591462b54))
* **ui:** camera selection on shared and owner match views ([#868](https://github.com/mandakan/splitsmith/issues/868)) ([78c915b](https://github.com/mandakan/splitsmith/commit/78c915b8f10c3a40cb1f21dad6ff628591b39944))


### Build / CI

* verify Railway deploys via deployment status, not the log stream ([#864](https://github.com/mandakan/splitsmith/issues/864)) ([86ef884](https://github.com/mandakan/splitsmith/commit/86ef8845256b3b5a2e2acddd328f1902d3d469e4)), closes [#863](https://github.com/mandakan/splitsmith/issues/863)

## [0.29.0](https://github.com/mandakan/splitsmith/compare/v0.28.0...v0.29.0) (2026-08-13)


### Features

* **sync:** shots become a first-class synced entity ([#848](https://github.com/mandakan/splitsmith/issues/848)) ([8ab16a8](https://github.com/mandakan/splitsmith/commit/8ab16a8f6079aa9c4f595567453460cb79b80a7d))


### Bug Fixes

* **test:** make the mode-resolution gate test load-independent ([#859](https://github.com/mandakan/splitsmith/issues/859)) ([99d3a4d](https://github.com/mandakan/splitsmith/commit/99d3a4d241bbbefd2442e3ba628da990488c2628))
* **ui:** a reset re-detect records the shots it wipes ([#856](https://github.com/mandakan/splitsmith/issues/856)) ([f067fba](https://github.com/mandakan/splitsmith/commit/f067fba36525327b128c4bc153525a0b1e0e1fbd)), closes [#842](https://github.com/mandakan/splitsmith/issues/842)
* **ui:** apply PATCH-returned WorkerView instead of refetching the roster ([#579](https://github.com/mandakan/splitsmith/issues/579)) ([#862](https://github.com/mandakan/splitsmith/issues/862)) ([0fb3025](https://github.com/mandakan/splitsmith/commit/0fb302578744077d051ee8be903f06a8f4aa919b))
* **ui:** candidate numbers are never reused within a stage ([#857](https://github.com/mandakan/splitsmith/issues/857)) ([d0113f1](https://github.com/mandakan/splitsmith/commit/d0113f1e0e9e18af18c024ebe8f532360e2338b6)), closes [#842](https://github.com/mandakan/splitsmith/issues/842)
* **ui:** deriveMarkers no longer double-emits a candidate-matched shot ([#852](https://github.com/mandakan/splitsmith/issues/852)) ([4a09719](https://github.com/mandakan/splitsmith/commit/4a09719799a4169d71d40dfdb2b1f9f4cca5c33f)), closes [#847](https://github.com/mandakan/splitsmith/issues/847)
* **ui:** hosted export downloads survive a reload ([#858](https://github.com/mandakan/splitsmith/issues/858)) ([86753e1](https://github.com/mandakan/splitsmith/commit/86753e11781021e794a19d0a60051b5d0ab5318a))
* **ui:** reject non-finite floats at the audit save boundary ([#853](https://github.com/mandakan/splitsmith/issues/853)) ([e1f2be8](https://github.com/mandakan/splitsmith/commit/e1f2be807b7039959f49dbebffb2b8dfeb39113c)), closes [#843](https://github.com/mandakan/splitsmith/issues/843)
* **ui:** repoint the coach PATCH at the by-id route ([#844](https://github.com/mandakan/splitsmith/issues/844)) ([ae4ee6a](https://github.com/mandakan/splitsmith/commit/ae4ee6ae8986b54dddb374e78a472f4ef0726606))
* **ui:** scope SyncCard to its own match ([#861](https://github.com/mandakan/splitsmith/issues/861)) ([4f2ae12](https://github.com/mandakan/splitsmith/commit/4f2ae121fe75f68138e7b0b164d53000caaf777b))


### Refactors

* **sync:** promote the names two sync modules already share ([#854](https://github.com/mandakan/splitsmith/issues/854)) ([7aabd60](https://github.com/mandakan/splitsmith/commit/7aabd60ce737419e52a4a820a2d887cc9b600bcb)), closes [#845](https://github.com/mandakan/splitsmith/issues/845)

## [0.28.0](https://github.com/mandakan/splitsmith/compare/v0.27.0...v0.28.0) (2026-08-12)


### Features

* compare share OG shell, meta and card - closes the compare unfurl gap ([95bad24](https://github.com/mandakan/splitsmith/commit/95bad242d1e3125f51650868e76ca0fb28d10053))
* moment badge on stage cards and new CompareCard model ([4aec17d](https://github.com/mandakan/splitsmith/commit/4aec17d3c76be8d9a64f64c394d0aed0b3a8bf71))
* moment deep links - share exact timestamps on video and compare pages ([0a8b3b6](https://github.com/mandakan/splitsmith/commit/0a8b3b6e394b013c5ab7a7383096fec3ae98dcb9))
* moment-aware stage OG cards, meta and shell forwarding ([eae4f3e](https://github.com/mandakan/splitsmith/commit/eae4f3e959056d44434b61a4d9c955133f587506))
* **ui:** apply moment deep links on the compare page ([d567641](https://github.com/mandakan/splitsmith/commit/d567641976fb1ca0f79689d234ddaaf4ecd56b54))
* **ui:** copy link at moment and track marker on the compare page ([8bbea46](https://github.com/mandakan/splitsmith/commit/8bbea46c047310fb093660f714685dcf873bbbcf))
* **ui:** moment marker, one-shot paused seek and copy hook in ResultsPlayer ([13cc1e1](https://github.com/mandakan/splitsmith/commit/13cc1e1baab301698612104b0c3511762d980942))
* **ui:** moment polish - dedicated marker color, who cap parity, re-arm on new moment, share-aware copy ([28470a5](https://github.com/mandakan/splitsmith/commit/28470a5dbfd7093d86186a6664532a541a40c863))
* **ui:** Moment type with URL serializer for timestamp deep links ([b1ce96d](https://github.com/mandakan/splitsmith/commit/b1ce96dcd1b1108d00b4fe3f2cccffb9812bec93))
* **ui:** share-aware copy-link-at-moment on owner pages ([0061ac3](https://github.com/mandakan/splitsmith/commit/0061ac361656f62db77f877f03e72c0d5202218d))
* **ui:** timestamp deep links on the results video page ([77ea8c5](https://github.com/mandakan/splitsmith/commit/77ea8c5c938b6b22e38df84d8d61302c5ce301a8))
* uncached moment-card render path and CompareCard dispatch ([7b59b4e](https://github.com/mandakan/splitsmith/commit/7b59b4ee1772f9295d9f13e1680444b5a6a2c141))


### Bug Fixes

* **moment:** dedicated color token, who cap parity, re-arm on moment change ([599c265](https://github.com/mandakan/splitsmith/commit/599c265100918f661f4f1e85a2b42ee103e465db))
* preserve RasterizerUnavailableError detail in share card render fallback ([8ec089d](https://github.com/mandakan/splitsmith/commit/8ec089dd510f9e33601eece951938539ae059bae))
* **share-card:** compare card headline carries shooter names, not "Compare" ([be67f77](https://github.com/mandakan/splitsmith/commit/be67f7765be7ed98a7a1591e0bd04424c1131c0a))
* **share:** validate compare stage number, widen png type, clamp moment t ([99d6b89](https://github.com/mandakan/splitsmith/commit/99d6b89c1eb8dce2ce63d24c71e8200eaf858b58))
* single ASCII dash in new test docstrings ([f5d6023](https://github.com/mandakan/splitsmith/commit/f5d6023dce6cd1b3337c182b95b5927fb0db466d))


### Documentation

* moment deep links design spec ([b06b61e](https://github.com/mandakan/splitsmith/commit/b06b61ea8b4c17093de2a5a5e55339d68605e422))
* moment deep links implementation plan ([84d2cb5](https://github.com/mandakan/splitsmith/commit/84d2cb575072d76ace81d19962d6f5a086db1ded))

## [0.27.0](https://github.com/mandakan/splitsmith/compare/v0.26.0...v0.27.0) (2026-08-12)


### Features

* add scope column to share_tokens, resolver surfaces it ([#779](https://github.com/mandakan/splitsmith/issues/779)) ([fc2eb34](https://github.com/mandakan/splitsmith/commit/fc2eb34cfefc9eaada8dce6e94c8f57f48783f09))
* match capability model - one table for guard and payload ([#756](https://github.com/mandakan/splitsmith/issues/756)) ([1be2a46](https://github.com/mandakan/splitsmith/commit/1be2a46c46a8032bbf55d90161adb6dc0ffd75b5))
* match writability as a server-derived capability ([#756](https://github.com/mandakan/splitsmith/issues/756)) ([1266623](https://github.com/mandakan/splitsmith/commit/1266623cab66cad1f0f54aba67bfca08afb939a3))
* read-scoped share requests run READ ONLY transactions ([#779](https://github.com/mandakan/splitsmith/issues/779)) ([ed19e06](https://github.com/mandakan/splitsmith/commit/ed19e06ce9e34283188c943ffe8bc2b67ae05b03))
* scope-keyed read-only defense for share requests ([#779](https://github.com/mandakan/splitsmith/issues/779)) ([bb763b5](https://github.com/mandakan/splitsmith/commit/bb763b5647769f491367a4c4f4c45a6dcb992f5b))
* serialize match capabilities on project, shooter-list, beep-queue payloads ([#756](https://github.com/mandakan/splitsmith/issues/756)) ([acbeb96](https://github.com/mandakan/splitsmith/commit/acbeb962a690510fecf349c74dc171586ca7c5aa))
* share middleware pins token scope in a db-layer ContextVar ([#779](https://github.com/mandakan/splitsmith/issues/779)) ([e7f2a5a](https://github.com/mandakan/splitsmith/commit/e7f2a5a0da4a077f87e556e045c952f1793d0284))
* state stores refuse mutations under read-scoped share requests ([#779](https://github.com/mandakan/splitsmith/issues/779)) ([9a2c7b3](https://github.com/mandakan/splitsmith/commit/9a2c7b3a43a9a8009ca94a2055d63d5905ecf2a4))
* **sync:** remote snippet GC - push deletes beep_review objects for reviewed videos ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([915959e](https://github.com/mandakan/splitsmith/commit/915959e2c484ce181f48f661aecf49c16f3087df))
* **ui:** capability-gate beep re-detect, exports, and trim rebuild ([#756](https://github.com/mandakan/splitsmith/issues/756)) ([7e5e2ed](https://github.com/mandakan/splitsmith/commit/7e5e2edb94f58b10823376df44aee79c9fdbcc91))
* **ui:** Home and Ingest gate edit affordances on the capability set ([#756](https://github.com/mandakan/splitsmith/issues/756)) ([123a1bd](https://github.com/mandakan/splitsmith/commit/123a1bda9fca92260470c766c6cb59f8821ef774))
* **ui:** MatchCapability type, capabilityDenied helper, capability-keyed banner ([#756](https://github.com/mandakan/splitsmith/issues/756)) ([f802cd8](https://github.com/mandakan/splitsmith/commit/f802cd80e26644a721cedecd7c57f5ef18e1db20))


### Bug Fixes

* ASCII single-dash + precise dep claim in share_guard import comment ([5bfd830](https://github.com/mandakan/splitsmith/commit/5bfd830f5b8d4066c1812249b915cae5928f811e))
* device-flow and shell hardening wave ([#734](https://github.com/mandakan/splitsmith/issues/734) [#735](https://github.com/mandakan/splitsmith/issues/735) [#736](https://github.com/mandakan/splitsmith/issues/736) [#737](https://github.com/mandakan/splitsmith/issues/737) [#738](https://github.com/mandakan/splitsmith/issues/738) [#739](https://github.com/mandakan/splitsmith/issues/739) [#725](https://github.com/mandakan/splitsmith/issues/725)) ([78e22f1](https://github.com/mandakan/splitsmith/commit/78e22f12cd672a30ef50c15e558f37cf7512412f))
* fail-closed share scope check + wider byte-identity net ([#779](https://github.com/mandakan/splitsmith/issues/779) review) ([7ab4128](https://github.com/mandakan/splitsmith/commit/7ab41282a131cc10300114124fc2da90e10890cc))
* final review wave - honest revoke-failure comment, stale-comment sweep, repoint ordering test ([#737](https://github.com/mandakan/splitsmith/issues/737) [#738](https://github.com/mandakan/splitsmith/issues/738)) ([2a5e326](https://github.com/mandakan/splitsmith/commit/2a5e3260edf165f00f0ec1d4f9454e77d85db45c))
* one honest proxy_ready everywhere; mirror copy stops promising a proxy ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([d11cc5b](https://github.com/mandakan/splitsmith/commit/d11cc5be62118a187f580f97f105fd9d70c74e14))
* scope beep-queue snippet listing to beep_review prefixes ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([5cabb02](https://github.com/mandakan/splitsmith/commit/5cabb020e336f711a0e39203c3f5ce8f0956ebfa))
* **sync:** beep-review sync follow-ups - snippet GC, honest proxy_ready, merge gating ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([5016549](https://github.com/mandakan/splitsmith/commit/5016549652a62375a3357879df5958a8f2999eae))
* **sync:** confirm-only beep writes merge without re-trim/re-detect ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([9db46a2](https://github.com/mandakan/splitsmith/commit/9db46a27f1a0b9928d84871d32593e244db2fea2))
* **sync:** per-subdir extension sets in the media key gate ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([4b1a02f](https://github.com/mandakan/splitsmith/commit/4b1a02fdeb340c5a9175e560773778810e893cca))
* **ui:** Add more click surfaces read-only message instead of silent no-op ([0ee33a3](https://github.com/mandakan/splitsmith/commit/0ee33a31be553297d743ff0135e49de1cbc75695))
* **ui:** close the [#836](https://github.com/mandakan/splitsmith/issues/836) pre-release findings (scoreboard gate, honest ingest copy) ([a563345](https://github.com/mandakan/splitsmith/commit/a563345e7453b516f622255c3165115b5ef00f79))
* **ui:** correct Compare share-mount comment, test the trim-rebuild capability gate ([607abaa](https://github.com/mandakan/splitsmith/commit/607abaa751b7e13223fa81de271187d7a208deb4))
* **ui:** gate hosted drop and Shooters page on edit capability ([#756](https://github.com/mandakan/splitsmith/issues/756) review) ([8f8d835](https://github.com/mandakan/splitsmith/commit/8f8d835286f2c2485f21c747cfd9c6ff8d244690))
* **ui:** gate scoreboard connect, disable mirror row controls, honest ingest copy ([#836](https://github.com/mandakan/splitsmith/issues/836)) ([fb2182c](https://github.com/mandakan/splitsmith/commit/fb2182c6f85366fcfcfd2223dc7b22a935ef3129))
* **ui:** mirror matches never arm the proxy poll; origin typed optional ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([c8a7cc5](https://github.com/mandakan/splitsmith/commit/c8a7cc5959d84af6eccbf48ffe67f1db0801eacd))
* **ui:** sign-out clears a stale load-failure flag ([#738](https://github.com/mandakan/splitsmith/issues/738)) ([9cf58bf](https://github.com/mandakan/splitsmith/commit/9cf58bf3722a1665ca127a3c4c8ff4d931b23d4e))
* **ui:** three-way origin capability mapping in MatchShell test helper ([3859684](https://github.com/mandakan/splitsmith/commit/385968496672071aa69489f71d2b6160e702a78b))


### Refactors

* mirror guard driven by the capability table ([#756](https://github.com/mandakan/splitsmith/issues/756)) ([b5cac43](https://github.com/mandakan/splitsmith/commit/b5cac4390d0cac985dcdd10e145eac066208eb9e))
* **ui:** BeepQueueResponse.origin is MatchOrigin; comment covers hosted ([#821](https://github.com/mandakan/splitsmith/issues/821)) ([1ad9331](https://github.com/mandakan/splitsmith/commit/1ad933180b3785c177940f2abb58b3c01bd07556))


### Documentation

* hardening wave implementation plans (PR 2 branch copy) ([497dd75](https://github.com/mandakan/splitsmith/commit/497dd7582536b7d0fd0dcaab9a20328ce87bc252))
* implementation plans for share-write foundation PRs A and B ([a99d59d](https://github.com/mandakan/splitsmith/commit/a99d59dd19e46259cafea844c8a2cf515c1c56fc))
* share-write foundation design spec ([#779](https://github.com/mandakan/splitsmith/issues/779) + [#756](https://github.com/mandakan/splitsmith/issues/756)) ([fd8e5ae](https://github.com/mandakan/splitsmith/commit/fd8e5ae37a25013e17b46d844a6ad24f1929b75e))

## [0.26.0](https://github.com/mandakan/splitsmith/compare/v0.25.0...v0.26.0) (2026-08-11)


### Features

* **admin-workers:** show GPU capabilities and flag outdated versions ([#830](https://github.com/mandakan/splitsmith/issues/830)) ([97cb2bf](https://github.com/mandakan/splitsmith/commit/97cb2bf37b338242da0f7172de86d90f3de26bd4))
* **results:** add per-stage compare CTA to share view and match summary ([#831](https://github.com/mandakan/splitsmith/issues/831)) ([7693e4a](https://github.com/mandakan/splitsmith/commit/7693e4a8a47786bf5a7105bfe5a60cc002e670e0))


### Documentation

* **workers:** fix native state-dir default + add PyPI/auto-update section ([#828](https://github.com/mandakan/splitsmith/issues/828)) ([f00bffe](https://github.com/mandakan/splitsmith/commit/f00bffe925f18caed1e46162d69a1b8f527e3014))

## [0.25.0](https://github.com/mandakan/splitsmith/compare/v0.24.0...v0.25.0) (2026-08-11)


### Features

* **audit:** desktop surfaces triage flag; audit save resolves it ([#823](https://github.com/mandakan/splitsmith/issues/823)) ([0afee37](https://github.com/mandakan/splitsmith/commit/0afee37905eebf847989b2c62a7ba06b8fcbe6f5))
* **coach:** exempt coach writes from the mirror read-only gate ([75f62d3](https://github.com/mandakan/splitsmith/commit/75f62d3050723eaa7d4044569feaee6d91e9a3fc))
* mobile audit triage surface (operator surfaces slice 4) ([162fc38](https://github.com/mandakan/splitsmith/commit/162fc3887ec80be574430becf68f356c4f83b137))
* mobile beep review (mobile operator surfaces slice 3) ([0fa1a1b](https://github.com/mandakan/splitsmith/commit/0fa1a1b603c6d076248a3c4742462a41e3ff58a5))
* mobile interval reclassify (slice 5) ([4fbe3eb](https://github.com/mandakan/splitsmith/commit/4fbe3ebc25c301a3da029b3a00ad7f42e5405f29))
* **sync:** generate beep review snippets for unconfirmed videos ([dfb2095](https://github.com/mandakan/splitsmith/commit/dfb2095607089baaf001a66f13a58f4524a5e642))
* **sync:** needs_attention LWW merge unit; exempt updated_at stamp from tripwire ([c54beac](https://github.com/mandakan/splitsmith/commit/c54beac9ccf253c9c02335eb4568b7f3c3cda7ca))
* **sync:** push beep review snippets with the media plan ([798e7bc](https://github.com/mandakan/splitsmith/commit/798e7bc505b303146a8ad79447fe8378af282135))
* **triage:** accept audit events count as audited status ([e63ce70](https://github.com/mandakan/splitsmith/commit/e63ce702d74c8d1a8164124aee07e71560fd0f78))
* **triage:** accept-stage endpoint with classification enforcement ([4b2a915](https://github.com/mandakan/splitsmith/commit/4b2a9152ef89aaa681f62a1642900860299f2164))
* **triage:** flag-for-desktop attention endpoint ([e4ee8d5](https://github.com/mandakan/splitsmith/commit/e4ee8d55e5c7369ee52ef0f61d468c2c3cd96187))
* **triage:** match triage aggregation endpoint ([fb1167d](https://github.com/mandakan/splitsmith/commit/fb1167d9f7a8ba477c428f79fd343ca6fed5968d))
* **triage:** mirror write gate admits accept and attention posts ([6979afb](https://github.com/mandakan/splitsmith/commit/6979afb50ba13ded965a624d8a0eb5d2973ab480))
* **triage:** summary endpoint and resolved threshold in payload ([#823](https://github.com/mandakan/splitsmith/issues/823)) ([42aca8f](https://github.com/mandakan/splitsmith/commit/42aca8f6d11f231295800c60b8cbc9d6740113e7))
* **ui:** awaiting-desktop-reprocess chip on stale stages ([a5dac8e](https://github.com/mandakan/splitsmith/commit/a5dac8ebde5628b609c2131d781132b6fb6104f1))
* **ui:** coach patch/undo builders + isShareView moved to lib ([ad4bd2c](https://github.com/mandakan/splitsmith/commit/ad4bd2cec7bed3aeae3f3ee0371967954091060b))
* **ui:** interval reclassify sheet + undo snackbar on ResultsStage ([9923b5a](https://github.com/mandakan/splitsmith/commit/9923b5a8c7a031d9e6d10eedab1f9333f8121f96))
* **ui:** mobile beep review card pager replaces DesktopGate ([ae4cc1e](https://github.com/mandakan/splitsmith/commit/ae4cc1e835296bfb80687065e7d64efd6f28bab3))
* **ui:** ReclassifySheet bottom sheet for interval classes ([470eb94](https://github.com/mandakan/splitsmith/commit/470eb944ce3f4b7d2225ad6bd87bf13182413264))
* **ui:** responsive stage triage surface at /match/:matchId/triage ([54fbd9c](https://github.com/mandakan/splitsmith/commit/54fbd9c77cd2f696e887a2068d37b45162d13c8c))
* **ui:** sheet busy feedback + roving tabindex for class chips ([eafd723](https://github.com/mandakan/splitsmith/commit/eafd723c5c899429ddf10f717cc0dded4c73dcdb))
* **ui:** Snackbar component with action button ([7422e01](https://github.com/mandakan/splitsmith/commit/7422e01d26d3b25c915fb2a2d44d2d202f675051))
* **ui:** tappable interval chips in SplitsList ([9f65800](https://github.com/mandakan/splitsmith/commit/9f658004bad676c2db5cb5ef154814fb9df98229))
* **ui:** triage api client types and mutations ([dc01649](https://github.com/mandakan/splitsmith/commit/dc01649f5d7c4960d9e01514e8004c70a74ea8d7))
* **ui:** triage nav item with flagged-for-desktop badge ([df2d895](https://github.com/mandakan/splitsmith/commit/df2d8953c8a3015c340f13df52a0832a6f5bfb18))


### Bug Fixes

* interval reclassify polish ([#826](https://github.com/mandakan/splitsmith/issues/826)) ([54f4b23](https://github.com/mandakan/splitsmith/commit/54f4b232810f66a8dc5813818f0b5b6d5600ed3f))
* **sync:** beep_review media keys pass sync validation; window + ffmpeg-error fixes ([89c6c9a](https://github.com/mandakan/splitsmith/commit/89c6c9a54f27cac3734a496bcb08b45c52c0198e))
* **sync:** needs_attention conflicts compare content, not stamps ([23a06d6](https://github.com/mandakan/splitsmith/commit/23a06d6f1d20c5919f92aff8e160bd9b10f70b00))
* **sync:** pull materializes metadata-only audit flags, tolerate naive ts ([a1bd5f5](https://github.com/mandakan/splitsmith/commit/a1bd5f59c717d80af47ad8dccae9bd171351dab8))
* triage follow-ups - flag on desktop Audit, cheap badge poll, threshold resolution ([#823](https://github.com/mandakan/splitsmith/issues/823)) ([b5df7a0](https://github.com/mandakan/splitsmith/commit/b5df7a0e4b6639b93dd00cb2c032fa093c884f0a))
* **triage:** beep-queue threshold resolution, gate pin tests, chip DOM cleanup ([#823](https://github.com/mandakan/splitsmith/issues/823)) ([17872b1](https://github.com/mandakan/splitsmith/commit/17872b110725beb01d719bf67f6c9021bed70ccf))
* **triage:** stub the doc-less flag so status doesn't stick in_progress ([d014b56](https://github.com/mandakan/splitsmith/commit/d014b569061d4ebfccda13ad1fd15bd221f5422d))
* **ui:** mirror banner constant stays sentence case - CSS uppercases the banner, apiErrorText reuses it inline ([f12c6b1](https://github.com/mandakan/splitsmith/commit/f12c6b13e9d61c40021156ccbf894e00dbcd7692))
* **ui:** mirror banner copy reflects phone-writable review actions ([16e4e53](https://github.com/mandakan/splitsmith/commit/16e4e53d949e095064f8a1189401a6bbf55fbdf8))
* **ui:** mobile beep review draft reset, timer cleanup, slider a11y ([fc3e5af](https://github.com/mandakan/splitsmith/commit/fc3e5af12890c90a08db522b7a7921b157706164))
* **ui:** stable Snackbar auto-dismiss timer + destructive error tone ([3dd41e8](https://github.com/mandakan/splitsmith/commit/3dd41e8c43f224a2df514af7cb5be7f19cf97258))
* **ui:** staleness chip ignores ignored videos and missing beeps ([30fc697](https://github.com/mandakan/splitsmith/commit/30fc697c2d7c0c3e5a845bb747815fdd37ed3e09))
* **ui:** trim both sides of coach note comparison; exact seek-row test name ([972bc1f](https://github.com/mandakan/splitsmith/commit/972bc1f033bf5f7982532413bdbe788dd6ad7cb7))
* **ui:** undo double-tap guard, stale-close guard, friendly patch error ([6cf7503](https://github.com/mandakan/splitsmith/commit/6cf7503a603e820d7ec0e3b94c5623a7c8dff79f))


### Refactors

* **ui:** BeepQueueResponse.origin is required, matching the backend ([88a32fa](https://github.com/mandakan/splitsmith/commit/88a32fa340474f4e483a88269970b2aaa2ca27fe))
* **ui:** extract useBeepQueue hook from BeepReview ([4b9d3b6](https://github.com/mandakan/splitsmith/commit/4b9d3b602c0bf1cbd3645882ae401dad0fc39370))
* **ui:** optional AnomalyChips onJump; MobileConfirmSheet ReactNode body ([4ff445e](https://github.com/mandakan/splitsmith/commit/4ff445eeb4ba669db225138671881e59b6be2ddd))
* **ui:** triage summary poll and payload-driven confidence threshold ([#823](https://github.com/mandakan/splitsmith/issues/823)) ([5a405fb](https://github.com/mandakan/splitsmith/commit/5a405fbcaae8d806c2469a4121c6f0eb64ed7273))


### Documentation

* mobile audit triage plan (slice 4) ([e722770](https://github.com/mandakan/splitsmith/commit/e722770f3364c0b5e9b1a16280a0b31337290bb1))
* mobile interval reclassify (slice 5) implementation plan ([8629024](https://github.com/mandakan/splitsmith/commit/86290242594eae66e250d0029767659642cc8bfb))
* reclassify polish ([#826](https://github.com/mandakan/splitsmith/issues/826)) implementation plan ([b5efced](https://github.com/mandakan/splitsmith/commit/b5efced525a62d27997599790c395ed309ce865e))
* triage follow-ups plan ([#823](https://github.com/mandakan/splitsmith/issues/823)) ([b45804a](https://github.com/mandakan/splitsmith/commit/b45804a82861d6cd7b628a3835c3335934608886))
* **ui:** update stale isShareView comments after lib move ([456739c](https://github.com/mandakan/splitsmith/commit/456739c4edd289ca587492a5622cb29b53f992a8))

## [0.24.0](https://github.com/mandakan/splitsmith/compare/v0.23.1...v0.24.0) (2026-08-11)


### Features

* **agent:** GPU agent image (NVENC + CUDA) for NVIDIA self-hosted hosts ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#809](https://github.com/mandakan/splitsmith/issues/809)) ([6db7092](https://github.com/mandakan/splitsmith/commit/6db70923680612abcc8b4238e37d242af90c1f50))
* **agent:** native (no-Docker) GPU agent for WSL2, zero env setup ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#810](https://github.com/mandakan/splitsmith/issues/810)) ([fa78737](https://github.com/mandakan/splitsmith/commit/fa78737d82364308cbbc97d25da75d7141778cfd))
* **agent:** opportunistic NVENC audit encoding + GPU capability advertisement ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#806](https://github.com/mandakan/splitsmith/issues/806)) ([5c233b2](https://github.com/mandakan/splitsmith/commit/5c233b210a423c1246aab9b4ad26f187d5501f1e))
* **db:** add retry for failed jobs in the postgres job backend ([f66017c](https://github.com/mandakan/splitsmith/commit/f66017c9447c4b57b3ba05b71762cb12a199b888))
* **db:** persist wire submit args on compute_jobs for retry ([b7c315a](https://github.com/mandakan/splitsmith/commit/b7c315adc9c721445852487bb3525237a93dd6aa))
* **ensemble:** CUDA execution provider for ONNX inference, opportunistic ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#808](https://github.com/mandakan/splitsmith/issues/808)) ([b210a69](https://github.com/mandakan/splitsmith/commit/b210a698c5b149c50a942277e66ed24b31717189))
* mobile-first jobs page with retry ([3b63306](https://github.com/mandakan/splitsmith/commit/3b633064ff9fddc642b0506eb741eaa320c701ca))
* retry endpoint for failed jobs ([dc9d38f](https://github.com/mandakan/splitsmith/commit/dc9d38ffc2231eee211576f866720f2a230d38a2))
* retry for failed jobs in the in-memory job registry ([62f5cda](https://github.com/mandakan/splitsmith/commit/62f5cdafe1cd7555050067d29954166a1408d803))
* **sync:** bidirectional pull-merge-push desktop sync ([6ab7180](https://github.com/mandakan/splitsmith/commit/6ab7180eed866d7de1ac79b6c68acfc9b493c048))
* **sync:** hosted doc manifest + GET routes, version-guarded PUTs ([60646da](https://github.com/mandakan/splitsmith/commit/60646dacfb4eb64e2577898ba37f83923fe7ae05))
* **sync:** pull planning via manifest version diff ([a3d2651](https://github.com/mandakan/splitsmith/commit/a3d265171a12216fff963df374ea2cc25a9151b2))
* **sync:** pull-merge-push orchestration with bounded conflict retry ([3714d6e](https://github.com/mandakan/splitsmith/commit/3714d6ecaf3cca13a8413228185aad367234b448))
* **sync:** pure three-way merge engine with conflict matrix ([87bcb3f](https://github.com/mandakan/splitsmith/commit/87bcb3f0632acc5890ad4f906fb61efc2b1afc5a))
* **sync:** remote-staleness hint on sync status + SyncCard ([18809cc](https://github.com/mandakan/splitsmith/commit/18809cce8753d46a3a403b7f200ad2fea0b22f85))
* **sync:** stamp ids on the MCP shot-detect audit_events append too ([263c6ac](https://github.com/mandakan/splitsmith/commit/263c6ac00752ceebbc24bb828ec06d54151f49b7))
* **sync:** stamp unique ids on audit_events entries ([cacafac](https://github.com/mandakan/splitsmith/commit/cacafac485bc711e8f5d63ecaf7a76387f3dbb93))
* **sync:** state-doc manifest query on ProjectStateStore ([86fdc7d](https://github.com/mandakan/splitsmith/commit/86fdc7d6c8a11e058d443f85f8806c9a02e25de0))
* **sync:** sync_state v2 doc_versions + sync_base snapshot store ([7b6c96f](https://github.com/mandakan/splitsmith/commit/7b6c96fd926e6b90af02f47f5d65732afcb7b6f1))
* **ui:** job timings type and retry action in the jobs data layer ([bb75166](https://github.com/mandakan/splitsmith/commit/bb75166b811f82255f50d031c60559b4a40b16f9))
* **ui:** jobs route, nav item and failed-count badge ([be19e13](https://github.com/mandakan/splitsmith/commit/be19e1326f67da839b6b285b381a4f78efbfcfa7))
* **ui:** mobile-first jobs page with retry and phase timings ([1059506](https://github.com/mandakan/splitsmith/commit/10595061ef1c43c733e60102fc907a699b4a13a4))


### Bug Fixes

* **docker:** GPU image build must not assert runtime CUDA on a GPU-less builder ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#813](https://github.com/mandakan/splitsmith/issues/813)) ([13a7e8c](https://github.com/mandakan/splitsmith/commit/13a7e8c0bae8e8b44c641e1c7982b16193949de0))
* **docker:** pin the onnxruntime-gpu swap to the project venv ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#815](https://github.com/mandakan/splitsmith/issues/815)) ([513d4d8](https://github.com/mandakan/splitsmith/commit/513d4d8fc5d8561cefc70c108ec1e5757b9a3bd0))
* **docker:** verify GPU wheel by provider .so, not dist metadata ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#814](https://github.com/mandakan/splitsmith/issues/814)) ([daf0b10](https://github.com/mandakan/splitsmith/commit/daf0b104b90b241c88543c6ab5f104c056a911d9))
* **jobs:** make retry's acknowledged flip an atomic claim ([8b0921a](https://github.com/mandakan/splitsmith/commit/8b0921ab8ac0b6d446b230f2c536122146934024))
* **jobs:** retry rebinds the original job's match context ([7c2ef83](https://github.com/mandakan/splitsmith/commit/7c2ef832e605682d618ad0ccb0f25cdda3f509d6))
* **sync:** base snapshot path must append .json, not replace key suffix ([8e28a87](https://github.com/mandakan/splitsmith/commit/8e28a87f812e97485a421a01858c25f4eb5aa02c))
* **sync:** malformed manifest degrades status hint to unknown ([ff7963a](https://github.com/mandakan/splitsmith/commit/ff7963a5897164b9afa5d0c71a5627f4a02a9657))
* **sync:** skip pulled audit docs with no local counterpart ([972139f](https://github.com/mandakan/splitsmith/commit/972139f98f17fa7de2339f9ab4ae6593acd789dd))
* **test:** seed_mirror sends required expected_version on doc PUT ([6e62a1e](https://github.com/mandakan/splitsmith/commit/6e62a1e13efa045d0c652253879607dbad7f219c))
* **test:** seed_mirror sends required expected_version on doc PUT ([098c185](https://github.com/mandakan/splitsmith/commit/098c1850065db1315c1eea19aa129dd6787934a5))
* **ui:** address Jobs page review findings ([1e9c9af](https://github.com/mandakan/splitsmith/commit/1e9c9af4fbcd44dea187d81d6881fc667a97e772))
* **ui:** match breadcrumb labels against the match-relative path, show shooter on job cards ([f49744e](https://github.com/mandakan/splitsmith/commit/f49744e86c775fafe85694b49abf0ef5850e0d0a))


### Documentation

* bidirectional sync slice design (mobile program slice 2) ([9f4ef9f](https://github.com/mandakan/splitsmith/commit/9f4ef9fb268be5b06e380bd61ff76cff197849ac))
* bidirectional sync slice implementation plan ([0ee2ef8](https://github.com/mandakan/splitsmith/commit/0ee2ef8a28b4f6f42691ad1d1b709f73c0d209f4))
* mobile operator surfaces spec + jobs page plan ([678aa20](https://github.com/mandakan/splitsmith/commit/678aa20354fd304a5b1c4314a5ec7300f7bb5936))


### Build / CI

* **publish-image:** publish the GPU agent image under -gpu tags ([#796](https://github.com/mandakan/splitsmith/issues/796)) ([#812](https://github.com/mandakan/splitsmith/issues/812)) ([3346588](https://github.com/mandakan/splitsmith/commit/3346588d2a45ab9c90454054da6f9390e4066d33))
* **test:** warm librosa/numba JIT cache before the xdist run ([#742](https://github.com/mandakan/splitsmith/issues/742)) ([#816](https://github.com/mandakan/splitsmith/issues/816)) ([164c919](https://github.com/mandakan/splitsmith/commit/164c919a678a1dbe427a967c3518982dc220c000))

## [0.23.1](https://github.com/mandakan/splitsmith/compare/v0.23.0...v0.23.1) (2026-08-10)


### Bug Fixes

* **ui:** coach heal persist 500s on slim local installs ([#804](https://github.com/mandakan/splitsmith/issues/804)) ([e3cc8a5](https://github.com/mandakan/splitsmith/commit/e3cc8a5597009f54bb82b9590c588ec8863dc045))

## [0.23.0](https://github.com/mandakan/splitsmith/compare/v0.22.1...v0.23.0) (2026-08-10)


### Features

* **ui:** add compare leaderboard rail for cockpit layout ([3429231](https://github.com/mandakan/splitsmith/commit/342923156aff6d935f874b864f99023c65069085))
* **ui:** add fused transport + timeline dock for compare cockpit ([e718824](https://github.com/mandakan/splitsmith/commit/e718824ec046caff6476f90e0c32e45c440f0802))
* **ui:** viewport-locked cockpit layout for compare view ([1158c2f](https://github.com/mandakan/splitsmith/commit/1158c2f29881816bfb1b71a98d984658b8396528))
* **ui:** viewport-locked cockpit layout for compare view ([0e8bab5](https://github.com/mandakan/splitsmith/commit/0e8bab5f52fdc24e44f3a317edaafb5dfe639524))


### Bug Fixes

* **sync:** skip unchanged docs on push, delta like media ([7c54aca](https://github.com/mandakan/splitsmith/commit/7c54aca988b462ac5e82a145af551ee0bc5cba56))
* **sync:** skip unchanged docs on push, delta like media ([1ae0d47](https://github.com/mandakan/splitsmith/commit/1ae0d47f65b38fb0e1c89ae87a32b873e5b5a275)), closes [#797](https://github.com/mandakan/splitsmith/issues/797)
* **ui:** bound the ingest workspace height so the clip list scrolls internally ([44da0c8](https://github.com/mandakan/splitsmith/commit/44da0c80ea2fa6b1de0bd1b134c16f384fdcf3f3))
* **ui:** bound the ingest workspace height so the clip list scrolls internally ([d6483d5](https://github.com/mandakan/splitsmith/commit/d6483d5be5bdd20fb6291b1142f967b3c954e8a2))
* **ui:** compare cockpit layout polish from visual verification ([3bb6a62](https://github.com/mandakan/splitsmith/commit/3bb6a622fe730c68c17e2eab60146a4338a773ba))
* **ui:** make secondary camera beeps reviewable and fix secondary focus playback ([49d944c](https://github.com/mandakan/splitsmith/commit/49d944ca25c73691ce470f1e96566a2398eff973))
* **ui:** scope share frame viewport lock to md+ viewports ([ee1b675](https://github.com/mandakan/splitsmith/commit/ee1b675ae565d8b7d7ada5cec2e920ba24c28988))
* **ui:** secondary camera beep review + trimmed-secondary focus playback ([2515e0d](https://github.com/mandakan/splitsmith/commit/2515e0d302d9e363d97fb3a893fbe918ea246410))
* **ui:** thin transport dock ruler ticks on long stages ([ac3aade](https://github.com/mandakan/splitsmith/commit/ac3aade1ab2ffe984bb31bdecd326528dc58dcfb))


### Refactors

* **ui:** pin share frame to viewport with scrolling middle region ([e111b2e](https://github.com/mandakan/splitsmith/commit/e111b2ec0fa443c889dfaae66c71e53b7d96a74c))


### Documentation

* implementation plan for compare cockpit layout ([1195ad1](https://github.com/mandakan/splitsmith/commit/1195ad1edf47a00713f3e2956ef613ff69f92bc3))

## [0.22.1](https://github.com/mandakan/splitsmith/compare/v0.22.0...v0.22.1) (2026-08-10)


### Bug Fixes

* **sync:** register pushed matches in the hosted picker ([4317948](https://github.com/mandakan/splitsmith/commit/4317948728da70e1881e5d53e016db42477fb008))
* **sync:** register pushed matches in the hosted picker ([eb6d2f8](https://github.com/mandakan/splitsmith/commit/eb6d2f8a95eca6268921cb7e1a8b47edbd83da50))
* **ui:** share pages skip the doomed /api/me fetch ([91558db](https://github.com/mandakan/splitsmith/commit/91558db6257e595c07f473fff3a9605f8278f6fb))
* **ui:** share pages skip the doomed /api/me fetch ([04ee5b8](https://github.com/mandakan/splitsmith/commit/04ee5b8985dc8467d8104ef421eea9f6f60e6e04))

## [0.22.0](https://github.com/mandakan/splitsmith/compare/v0.21.0...v0.22.0) (2026-08-10)


### Features

* **compare:** log max observed sync drift per playback session ([ff98292](https://github.com/mandakan/splitsmith/commit/ff9829227159ff3a4a6a39f4f9ecdabe554450ec))
* **compare:** resolve trims through storage, expose logical video_ref ([e486d72](https://github.com/mandakan/splitsmith/commit/e486d7216e2517c48efbbf2a32cd96c6a7f37089))
* **compare:** stream fallback takes logical refs, serves hosted via presign ([baa27d2](https://github.com/mandakan/splitsmith/commit/baa27d24f90937ec00bc74d4a635c1eb266d84bd))
* **overlay:** give single-shooter exports the sprite engine and shared clock ([#758](https://github.com/mandakan/splitsmith/issues/758)) ([ea3565e](https://github.com/mandakan/splitsmith/commit/ea3565ed514ace21282e260d950732a8b30284d2))
* **share:** allowlist stage compare + ref streaming, strip coach notes for viewers ([1a88efa](https://github.com/mandakan/splitsmith/commit/1a88efaf32875f2d9aae00edf5703794d1a3309f))
* **share:** compare view behind share links ([#700](https://github.com/mandakan/splitsmith/issues/700) MVP) ([1cf9bbd](https://github.com/mandakan/splitsmith/commit/1cf9bbd599b838cdb07a2989ca2d26ad7bac2c33))
* **share:** Open Graph preview images for share links ([#786](https://github.com/mandakan/splitsmith/issues/786)) ([4f032d9](https://github.com/mandakan/splitsmith/commit/4f032d9bef6f3b0243c6db8519c54c5c33a3bf6e))
* **ui:** compare view behind share links - desktop-only, read-only ([43875a9](https://github.com/mandakan/splitsmith/commit/43875a9e5a4d86f89215312deead10663b361c62))


### Bug Fixes

* align unclassified split-stat fallback with the auto-classifier ([#776](https://github.com/mandakan/splitsmith/issues/776)) ([abdeba2](https://github.com/mandakan/splitsmith/commit/abdeba2e9cb98f08b95ebf67404eca484c1fa137))
* audited stages are always fully classified ([#775](https://github.com/mandakan/splitsmith/issues/775)) ([6d963b6](https://github.com/mandakan/splitsmith/commit/6d963b6aa91143c6c93d090532daa24d0fb43733))
* classify intervals on audit save so audited stages are fully classified ([#775](https://github.com/mandakan/splitsmith/issues/775)) ([daf0ce6](https://github.com/mandakan/splitsmith/commit/daf0ce697e45bbeb2fe2eb0f15bca6832ee1036f))
* coach GET backfills interval classes on legacy docs, in-memory for share reads ([#775](https://github.com/mandakan/splitsmith/issues/775)) ([adbcdce](https://github.com/mandakan/splitsmith/commit/adbcdce855b4750e19373a613fbe091c3ec6bb62))
* **compare:** carry interval classes on the stage compare payload ([4d119c5](https://github.com/mandakan/splitsmith/commit/4d119c522a5b63527cf8d792c9595cc82bb2f100))
* **compare:** compose summary stills at the grid's composed size ([a1eb917](https://github.com/mandakan/splitsmith/commit/a1eb917ec6c854b9f8f3984153070a492e9c2908))
* **compare:** compose summary stills at the grid's composed size ([#691](https://github.com/mandakan/splitsmith/issues/691)) ([45d79a2](https://github.com/mandakan/splitsmith/commit/45d79a28b4bef93988b26ded892bd51a179278c5))
* **compare:** RankingTable follows the unified split rule ([e45172b](https://github.com/mandakan/splitsmith/commit/e45172b92f6a682e6d0c45f652f86131b4d61395))
* **exports:** one function names a stage's files, so readers stop missing them ([#768](https://github.com/mandakan/splitsmith/issues/768)) ([b2edbc4](https://github.com/mandakan/splitsmith/commit/b2edbc4bc25c7329733dd91344b9f0dc48639029))
* heal partial classification in overlay loader and clear_class patch, harden backfill guard ([#775](https://github.com/mandakan/splitsmith/issues/775)) ([6038eaf](https://github.com/mandakan/splitsmith/commit/6038eafd1f61ad98ef0fc5cd39373b422f1a5dac))
* **overlay:** resolve the auto codec through the injected probe runner ([#770](https://github.com/mandakan/splitsmith/issues/770)) ([0a3a031](https://github.com/mandakan/splitsmith/commit/0a3a0313a5937cfc3876f154a63550acf970c709))
* split statistics count only split-classed intervals, page shows the draw ([#774](https://github.com/mandakan/splitsmith/issues/774)) ([5e53846](https://github.com/mandakan/splitsmith/commit/5e538466415aac466f6ce0649c8af4e771336ac8)), closes [#772](https://github.com/mandakan/splitsmith/issues/772)
* **ui:** add splitsFromTimeline pairing gaps with interval classes ([374abb1](https://github.com/mandakan/splitsmith/commit/374abb194c44fd8006a2737cc921e118917d8fff))
* **ui:** compose Lab fixture slugs the way the backend reads them ([#771](https://github.com/mandakan/splitsmith/issues/771)) ([f6ea18e](https://github.com/mandakan/splitsmith/commit/f6ea18ebe4f875237c22deb32f6f9d7a476ecd80))
* **ui:** global bar and account chips fit a phone ([#733](https://github.com/mandakan/splitsmith/issues/733)) ([#790](https://github.com/mandakan/splitsmith/issues/790)) ([4ddce6f](https://github.com/mandakan/splitsmith/commit/4ddce6f35267ff82a19090c28993e64ecd2eff64))
* **ui:** hide operator-only Export FCPXML button and use neutral banner copy on share views ([5c491b7](https://github.com/mandakan/splitsmith/commit/5c491b7d66391ca6e69b59a337ae77791a80a91c))
* **ui:** offer the overlay codecs the backend actually accepts ([#765](https://github.com/mandakan/splitsmith/issues/765)) ([0f64819](https://github.com/mandakan/splitsmith/commit/0f6481999e069947197ccf61478761a9026f668a)), closes [#761](https://github.com/mandakan/splitsmith/issues/761)
* **ui:** RankingTable follows the unified split rule, adds Draw ([a6bdb44](https://github.com/mandakan/splitsmith/commit/a6bdb44fea58a2960c39170c7ba45a10c929ea9a))


### Performance

* **overlay:** key the ffmpeg capability probe on the font's bytes ([#764](https://github.com/mandakan/splitsmith/issues/764)) ([ed3a7d5](https://github.com/mandakan/splitsmith/commit/ed3a7d5f191d0ad462e4af80c19597587d3d47c9)), closes [#762](https://github.com/mandakan/splitsmith/issues/762)


### Refactors

* **coach:** one heal_unclassified guard for all four surfaces ([#789](https://github.com/mandakan/splitsmith/issues/789)) ([5e6884f](https://github.com/mandakan/splitsmith/commit/5e6884fc0df90331920ad4fa07b036572c633bec)), closes [#780](https://github.com/mandakan/splitsmith/issues/780)
* **compare:** move audit reading into a core module and cut the cycle ([#767](https://github.com/mandakan/splitsmith/issues/767)) ([60e1cfe](https://github.com/mandakan/splitsmith/commit/60e1cfe79160df28f5ec23caade5fd20cdb4d7c0)), closes [#760](https://github.com/mandakan/splitsmith/issues/760)
* **overlay:** remove the PIL text machinery nothing calls any more ([#766](https://github.com/mandakan/splitsmith/issues/766)) ([c0c09b1](https://github.com/mandakan/splitsmith/commit/c0c09b13031ddaaa9acc48e73f0cf981459210f9)), closes [#759](https://github.com/mandakan/splitsmith/issues/759)
* **project:** the match-project model is core, not part of the web UI ([#769](https://github.com/mandakan/splitsmith/issues/769)) ([21487e6](https://github.com/mandakan/splitsmith/commit/21487e603fb832472b42961dc96f8cb1b88036af))
* **ui:** drop Coach mount-time auto-reclassify, backend guarantees classified stages ([#775](https://github.com/mandakan/splitsmith/issues/775)) ([e4807a3](https://github.com/mandakan/splitsmith/commit/e4807a322ce283001bf56b90347e7b1b124034b9))


### Documentation

* **compare:** correct still-size wording after the composed-size fix ([#691](https://github.com/mandakan/splitsmith/issues/691)) ([38beea2](https://github.com/mandakan/splitsmith/commit/38beea294ccde47676a9a8582a8fb86bff536ef1))
* design for [#781](https://github.com/mandakan/splitsmith/issues/781) (RankingTable unified splits) ([37784dc](https://github.com/mandakan/splitsmith/commit/37784dc4a0e4ec884fc32a86069ce6d6b2be9908))
* design for the [#700](https://github.com/mandakan/splitsmith/issues/700) compare share MVP ([76fea29](https://github.com/mandakan/splitsmith/commit/76fea29ea619e473d280a909b803cc7fb660aba7))
* design spec for [#775](https://github.com/mandakan/splitsmith/issues/775) classify-on-audit-save ([a3bfb19](https://github.com/mandakan/splitsmith/commit/a3bfb19f102c7284dc66ddfb9f9cdea83c5810dc))
* implementation plan for [#775](https://github.com/mandakan/splitsmith/issues/775) classify-on-audit-save ([fe529cd](https://github.com/mandakan/splitsmith/commit/fe529cdbecf584aa886d4304cee1bc39d4c4f2a5))
* implementation plan for [#781](https://github.com/mandakan/splitsmith/issues/781); spec aligned with StageStats reality ([590ca22](https://github.com/mandakan/splitsmith/commit/590ca224654e88f53333329a153e61ac5140c2ff))
* implementation plan for the [#700](https://github.com/mandakan/splitsmith/issues/700) compare share MVP ([0e352ed](https://github.com/mandakan/splitsmith/commit/0e352edfa7d934eb8f45527acf28eff555154767))
* **overlay:** kickoff for the four [#684](https://github.com/mandakan/splitsmith/issues/684) follow-ups ([#763](https://github.com/mandakan/splitsmith/issues/763)) ([4dcf56a](https://github.com/mandakan/splitsmith/commit/4dcf56a9da85afc8ad60620ce1ab21afe2a06c77))
* plan for composing summary stills at the composed grid size ([#691](https://github.com/mandakan/splitsmith/issues/691)) ([bf5abaf](https://github.com/mandakan/splitsmith/commit/bf5abaf0e78a6582cdfd9edd7fbd9ff68e2811dc))
* state the [#775](https://github.com/mandakan/splitsmith/issues/775) full-classification invariant at both statistic_splits mirrors ([24e05a4](https://github.com/mandakan/splitsmith/commit/24e05a4209daa598a8d7072c230d9b2422b39429))

## [0.21.0](https://github.com/mandakan/splitsmith/compare/v0.20.1...v0.21.0) (2026-08-08)


### Features

* browser-assisted desktop auth ([#719](https://github.com/mandakan/splitsmith/issues/719)) ([0facc1a](https://github.com/mandakan/splitsmith/commit/0facc1ae071a91c1a0bfcdcd3745b997927032cb))
* **compare:** show each tile's stage summary from its own footage end ([#744](https://github.com/mandakan/splitsmith/issues/744)) ([49321f8](https://github.com/mandakan/splitsmith/commit/49321f8aa74cfafa6c49e5c376980c59f983528d))
* **ui:** add depth-counted file-drag tracking util ([ce0daf7](https://github.com/mandakan/splitsmith/commit/ce0daf75443eabe935cdae437d97c1c6307c5c8f))
* **ui:** add-videos UX rework - mode-gated drops + single-scroll folder picker ([19b310c](https://github.com/mandakan/splitsmith/commit/19b310ca474ac8573ddfee94789868cfb1eb2c61))
* **ui:** expose deployment-mode resolution state from useDeploymentMode ([45f50e2](https://github.com/mandakan/splitsmith/commit/45f50e23942e3cfc9c71f02ae0a0f1fc3453962f))
* **ui:** guard the SPA against unhandled file drops ([7b862eb](https://github.com/mandakan/splitsmith/commit/7b862eb460b88d85174f45b30cb72dcd441b7cb9))
* **ui:** mode-gate the ingest empty state and add hosted full-page drop ([3bddaec](https://github.com/mandakan/splitsmith/commit/3bddaecb8e465bacc072d5c4063d8d52d3e9c139))
* **ui:** rewrite FolderPicker as a single-scroll picker dialog ([4f321e3](https://github.com/mandakan/splitsmith/commit/4f321e37c635e881304bcde326cbb76cfb0c6db7))


### Bug Fixes

* **ui:** clear hosted drop overlay on modal drops, storage toggle a11y, polish ([bc80ffc](https://github.com/mandakan/splitsmith/commit/bc80ffc8546a184817bc8d499743d8ad88089d9e))
* **ui:** decrement drag depth unconditionally on dragleave ([39be313](https://github.com/mandakan/splitsmith/commit/39be31334d55682f252fcc053c296062a485667f))
* **ui:** folder picker escape scoping, error gating, busy announcements ([fdeadaf](https://github.com/mandakan/splitsmith/commit/fdeadafa37477df3d25d1be9557aa78828e97f82))
* **ui:** migrate HostedAccountChip to resolved deployment-mode state ([aa8bf3b](https://github.com/mandakan/splitsmith/commit/aa8bf3bad958a0a624c1902e903c5beae90a88cf))


### Refactors

* **ui:** extract HostedUploadModal with depth-counted dropzone ([0757d99](https://github.com/mandakan/splitsmith/commit/0757d993ab4cdf27591cd222d1b7f5e3f4f75466))
* **ui:** one-shot local add-footage, drop queue and picker facades ([f848a02](https://github.com/mandakan/splitsmith/commit/f848a025e6471c1cffa6d9a3ee17a147d453c245))
* **ui:** single-dash comments in HostedUploadModal ([0147e48](https://github.com/mandakan/splitsmith/commit/0147e481f7c26e1c159a0ccd3d6abb09753873f7))
* **ui:** sweep stale add-footage references and comments ([543362d](https://github.com/mandakan/splitsmith/commit/543362df085acc65315af1b40aed6d7d86f381e2))


### Documentation

* add-videos UX rework design (mode-gated drops, single-scroll picker) ([d746d48](https://github.com/mandakan/splitsmith/commit/d746d48b57589832be3b06bd85a00494fd83b1e3))
* add-videos UX rework implementation plan ([57f1caa](https://github.com/mandakan/splitsmith/commit/57f1caafeea2afd0d7adf9e4ab9f516bf4e47115))
* keep empty-looking folder picks valid for add-footage (recursive scan) ([eb8aaa0](https://github.com/mandakan/splitsmith/commit/eb8aaa01ed8c530f58d1237763571e26f44f65c2))

## [0.20.1](https://github.com/mandakan/splitsmith/compare/v0.20.0...v0.20.1) (2026-08-08)


### Build / CI

* **deps:** batch all outstanding dependabot updates ([2e2d2cd](https://github.com/mandakan/splitsmith/commit/2e2d2cdaa7751899649847b2f170d0877fc029d5))
* **deps:** batch all outstanding dependabot updates ([f716ceb](https://github.com/mandakan/splitsmith/commit/f716ceb24c1934286da0e182c72d24bef46f9d4e))

## [0.20.0](https://github.com/mandakan/splitsmith/compare/v0.19.0...v0.20.0) (2026-08-08)


### Features

* **ui:** share results full width on mobile, empty-stage collapse + hit counts ([#730](https://github.com/mandakan/splitsmith/issues/730)) ([81b5752](https://github.com/mandakan/splitsmith/commit/81b5752f7cb969db9572371bc9e8379729370f35))


### Documentation

* kickoff for [#719](https://github.com/mandakan/splitsmith/issues/719), with what moved under the spec since it was written ([#729](https://github.com/mandakan/splitsmith/issues/729)) ([e4c2552](https://github.com/mandakan/splitsmith/commit/e4c2552ce8db9491358c07aab064e0177ad4ce71))
* record the RootLayout refactor in the 0.19.0 changelog ([#727](https://github.com/mandakan/splitsmith/issues/727)) ([0d95088](https://github.com/mandakan/splitsmith/commit/0d95088178d2631d6d8a4573a8d767a29010b9a8))

## [0.19.0](https://github.com/mandakan/splitsmith/compare/v0.18.0...v0.19.0) (2026-08-08)


### Features

* **ui:** brand the public share surface + document share links ([#722](https://github.com/mandakan/splitsmith/issues/722)) ([ba81b4e](https://github.com/mandakan/splitsmith/commit/ba81b4ed740d746819707c202328896c34d41506))


### Refactors

* **ui:** a RootLayout that owns global chrome ([#550](https://github.com/mandakan/splitsmith/issues/550)) ([#724](https://github.com/mandakan/splitsmith/issues/724)) ([be602ae](https://github.com/mandakan/splitsmith/commit/be602ae))


### Documentation

* design for browser-assisted desktop auth ([#719](https://github.com/mandakan/splitsmith/issues/719)) ([#726](https://github.com/mandakan/splitsmith/issues/726)) ([8699f9b](https://github.com/mandakan/splitsmith/commit/8699f9bfd7198349dbe4bfe84a016b5e6bbd6077))

## [0.18.0](https://github.com/mandakan/splitsmith/compare/v0.17.0...v0.18.0) (2026-08-07)


### Features

* **ui:** share view polish - row affordance, back link, shooter switcher, timer freeze ([#720](https://github.com/mandakan/splitsmith/issues/720)) ([1464041](https://github.com/mandakan/splitsmith/commit/1464041a51bb61c2a4fe58e87fd5ff0605a4da7d))

## [0.17.0](https://github.com/mandakan/splitsmith/compare/v0.16.0...v0.17.0) (2026-08-07)


### Features

* **sync:** concurrent part PUTs in upload_media ([#713](https://github.com/mandakan/splitsmith/issues/713)) ([#715](https://github.com/mandakan/splitsmith/issues/715)) ([34f243e](https://github.com/mandakan/splitsmith/commit/34f243ec8b0d6185c1cb331c2c87e97229cda06d))
* **sync:** desktop-to-hosted match push MVP ([#631](https://github.com/mandakan/splitsmith/issues/631)) ([#707](https://github.com/mandakan/splitsmith/issues/707)) ([379c545](https://github.com/mandakan/splitsmith/commit/379c5454a4b3e749e729ee04dfde26af936a3570))
* **sync:** phase timings + per-item upload metrics on push ([#631](https://github.com/mandakan/splitsmith/issues/631)) ([#710](https://github.com/mandakan/splitsmith/issues/710)) ([ae7edea](https://github.com/mandakan/splitsmith/commit/ae7edea7f702e0af4d05cc0c3a2f11c4c546c6c3))


### Bug Fixes

* **hosted:** magic-link log line reaches Railway with an empty message ([#711](https://github.com/mandakan/splitsmith/issues/711)) ([#714](https://github.com/mandakan/splitsmith/issues/714)) ([765bc58](https://github.com/mandakan/splitsmith/commit/765bc589f0850842652957b431d4ade2a82e995c))
* **sync:** HostedSyncClient owns the /api/sync prefix; base_url is the bare origin ([#712](https://github.com/mandakan/splitsmith/issues/712)) ([c347053](https://github.com/mandakan/splitsmith/commit/c3470533b43be17bfac43a1d65adbdbe499fb11e))
* **ui:** deselect the pre-selection race in the MatchExport stage test ([#718](https://github.com/mandakan/splitsmith/issues/718)) ([de25775](https://github.com/mandakan/splitsmith/commit/de25775d64dd83a41731bd134f936b9b241ac12a))


### Documentation

* **observability:** correct the [#711](https://github.com/mandakan/splitsmith/issues/711) comments after the live post-deploy check ([#717](https://github.com/mandakan/splitsmith/issues/717)) ([b4b26d6](https://github.com/mandakan/splitsmith/commit/b4b26d66c3a415d5e54280ca14661c5ca43e3785))
* **overlay:** kickoff for [#684](https://github.com/mandakan/splitsmith/issues/684), with the issue's stale premises corrected ([#709](https://github.com/mandakan/splitsmith/issues/709)) ([276aad5](https://github.com/mandakan/splitsmith/commit/276aad5ecc92868f97b345ebf30ad3c1343cf573))
* **testing:** document -n0 for multi-file pytest -m docker runs ([#716](https://github.com/mandakan/splitsmith/issues/716)) ([d9e054d](https://github.com/mandakan/splitsmith/commit/d9e054dbd6e383424a175996c2763216968e06d7))

## [0.16.0](https://github.com/mandakan/splitsmith/compare/v0.15.0...v0.16.0) (2026-08-07)


### Features

* **compare:** burn an opt-in splits overlay into the grid MP4 ([#677](https://github.com/mandakan/splitsmith/issues/677)) ([ef51e06](https://github.com/mandakan/splitsmith/commit/ef51e0635ece6a76f084e4cb3c99fea09ad2e89a))
* **compare:** hold a frozen stage summary at the end of each stage ([#687](https://github.com/mandakan/splitsmith/issues/687)) ([3566c54](https://github.com/mandakan/splitsmith/commit/3566c54f5e1233668c3873384eadd09293d1fa5e))
* **compare:** render the multi-shooter grid directly to MP4 (phase 0) ([#674](https://github.com/mandakan/splitsmith/issues/674)) ([2e0370e](https://github.com/mandakan/splitsmith/commit/2e0370e11fd2869277aaa57accd87e45a6f51fda))
* **compare:** ship a merged audio track as the grid's default ([#675](https://github.com/mandakan/splitsmith/issues/675)) ([b24f71f](https://github.com/mandakan/splitsmith/commit/b24f71f82b309d099fd981f0923de5a0d57fb7ab))
* **ensemble:** export voters C and E to ONNX, drop the sklearn pickle coupling ([#649](https://github.com/mandakan/splitsmith/issues/649)) ([#661](https://github.com/mandakan/splitsmith/issues/661)) ([b472172](https://github.com/mandakan/splitsmith/commit/b472172d445115c8231383e3f2130ab6f20bba60))
* **ui:** queue-level upload progress with an ETA ([#556](https://github.com/mandakan/splitsmith/issues/556)) ([#657](https://github.com/mandakan/splitsmith/issues/657)) ([84b1d4e](https://github.com/mandakan/splitsmith/commit/84b1d4e2f5c244c9f01697b20505f3b892646c10))


### Bug Fixes

* **audit:** stop a focused rejected marker from force-showing the whole rejected layer ([#685](https://github.com/mandakan/splitsmith/issues/685)) ([67416dc](https://github.com/mandakan/splitsmith/commit/67416dc73cf988f04361bbc63f74b1bb966c1315)), closes [#666](https://github.com/mandakan/splitsmith/issues/666)
* **compare:** pre-flight the overlay's ffmpeg, and remove the live delta strip ([#678](https://github.com/mandakan/splitsmith/issues/678)) ([277acf5](https://github.com/mandakan/splitsmith/commit/277acf505a89e24267d95fb5f234985213680b0a))
* **jobs:** jobs carry shooter identity, so one shooter's detection no longer blocks another's ([#688](https://github.com/mandakan/splitsmith/issues/688)) ([bfd804f](https://github.com/mandakan/splitsmith/commit/bfd804f0a10cae72f11270245f6b2ecaf3dff564))
* **jobs:** journal the local job queue so a killed app re-enqueues it on restart ([#665](https://github.com/mandakan/splitsmith/issues/665)) ([#694](https://github.com/mandakan/splitsmith/issues/694)) ([cf709bc](https://github.com/mandakan/splitsmith/commit/cf709bce6c4efec52d6105bd968c038111424b6d))
* **trim:** rebuild missing trim caches for every angle, not just the primary ([#351](https://github.com/mandakan/splitsmith/issues/351)) ([#655](https://github.com/mandakan/splitsmith/issues/655)) ([f105677](https://github.com/mandakan/splitsmith/commit/f105677da36f7a0d9a8847b6a31f1effe4040514))
* **ui:** dedup failed-shooter errors and stop rendering raw API bodies ([#660](https://github.com/mandakan/splitsmith/issues/660)) ([5c947ef](https://github.com/mandakan/splitsmith/commit/5c947ef653410ca6c3eb60389fe06113de7d1b4b)), closes [#651](https://github.com/mandakan/splitsmith/issues/651)
* **ui:** refresh sidebar stage status when a background job finishes ([#663](https://github.com/mandakan/splitsmith/issues/663)) ([#697](https://github.com/mandakan/splitsmith/issues/697)) ([710a674](https://github.com/mandakan/splitsmith/commit/710a674c7e69feadd10ad16f919b8bcb525f07e6))


### Performance

* **ui:** read the calibration directly for /api/calibrated-camera-models ([#667](https://github.com/mandakan/splitsmith/issues/667)) ([#669](https://github.com/mandakan/splitsmith/issues/669)) ([9aed108](https://github.com/mandakan/splitsmith/commit/9aed108bbf8b1e1b45fc5a9b1e3e8e75109f3867))


### Refactors

* **hosted:** derive raw-video ownership in one place ([#562](https://github.com/mandakan/splitsmith/issues/562)) ([#659](https://github.com/mandakan/splitsmith/issues/659)) ([ecd01ac](https://github.com/mandakan/splitsmith/commit/ecd01ac966f9c24b450d1cc71d50aca7b4dac40c))
* **overlay:** a composition seam, a box engine, and a summary built around splits ([#683](https://github.com/mandakan/splitsmith/issues/683)) ([#703](https://github.com/mandakan/splitsmith/issues/703)) ([70a118d](https://github.com/mandakan/splitsmith/commit/70a118d1eb03b22049f9ee69b1105c706c1aa27a))
* **overlay:** the live sprites go through the box engine too ([#693](https://github.com/mandakan/splitsmith/issues/693)) ([#706](https://github.com/mandakan/splitsmith/issues/706)) ([46b568a](https://github.com/mandakan/splitsmith/commit/46b568a0daceb40d5d8af5bce31e399104c9796e))


### Documentation

* amend milestone B for the summary ranking and the clock bleed ([b6732de](https://github.com/mandakan/splitsmith/commit/b6732ded018cf27f36689b4ce4dc5a382c87805d))
* kickoff for the compare-grid stage summary hold (milestone B) ([ec32be2](https://github.com/mandakan/splitsmith/commit/ec32be28758624e1d4602c4349524c34b8fc46ca))
* kickoff for the honest fixture and render-frames tool ([#682](https://github.com/mandakan/splitsmith/issues/682)) ([ea8dcf3](https://github.com/mandakan/splitsmith/commit/ea8dcf3e8e46f9f47f7c602daa3ea599a2948bb6))
* kickoff for the overlay composition seam ([#683](https://github.com/mandakan/splitsmith/issues/683)) ([1856704](https://github.com/mandakan/splitsmith/commit/1856704d859b39455ae6e3cfbdf4a401a20bd312))
* **overlay:** the design docstrings now cite a file that exists ([#704](https://github.com/mandakan/splitsmith/issues/704)) ([7fb4089](https://github.com/mandakan/splitsmith/commit/7fb40896b5a3cc5abd82b47f97b815ec2ca18c98))
* phase 1 kickoff for the compare-grid splits overlay ([d5ad724](https://github.com/mandakan/splitsmith/commit/d5ad72492e9b6b4810317e5f4c05bdaa784cb20f))
* spec how the live overlay and the stage summary share the frame ([0604426](https://github.com/mandakan/splitsmith/commit/0604426cc04167083920c0e0e119fab93b60367f))


### Build / CI

* install ffmpeg and fail the build when integration tests skip ([#670](https://github.com/mandakan/splitsmith/issues/670)) ([#671](https://github.com/mandakan/splitsmith/issues/671)) ([81d6862](https://github.com/mandakan/splitsmith/commit/81d68627014b6c3479349b7b48df9055f8319e21))
* validate the SPA and the wheel on every PR ([#647](https://github.com/mandakan/splitsmith/issues/647)) ([#658](https://github.com/mandakan/splitsmith/issues/658)) ([e3b0230](https://github.com/mandakan/splitsmith/commit/e3b0230e9eb00552a07c341440e7c69dbcaf4df8))

## [0.15.0](https://github.com/mandakan/splitsmith/compare/v0.14.1...v0.15.0) (2026-08-03)


### Features

* **ui:** stage-list editor in the SPA ([#652](https://github.com/mandakan/splitsmith/issues/652)) ([47896cd](https://github.com/mandakan/splitsmith/commit/47896cd0acfee777ab35a9fa2404b0a1ba455dfa))

## [0.14.1](https://github.com/mandakan/splitsmith/compare/v0.14.0...v0.14.1) (2026-08-03)


### Bug Fixes

* **deps:** pin scikit-learn &lt;1.9 so the ensemble artifacts load ([#648](https://github.com/mandakan/splitsmith/issues/648)) ([d9fa707](https://github.com/mandakan/splitsmith/commit/d9fa707e97cf803aae0d41e133622c46e7e6a2dd))

## [0.14.0](https://github.com/mandakan/splitsmith/compare/v0.13.1...v0.14.0) (2026-08-03)


### Bug Fixes

* **packaging:** ship the built SPA in the wheel ([#643](https://github.com/mandakan/splitsmith/issues/643)) ([393942a](https://github.com/mandakan/splitsmith/commit/393942af97248476d55d1d7e880a958467a2b437)), closes [#642](https://github.com/mandakan/splitsmith/issues/642)

## [0.13.1](https://github.com/mandakan/splitsmith/compare/v0.13.0...v0.13.1) (2026-08-03)


### Bug Fixes

* **deploy:** retry the startup migration's DB connect ([#559](https://github.com/mandakan/splitsmith/issues/559)) ([#635](https://github.com/mandakan/splitsmith/issues/635)) ([58b20fa](https://github.com/mandakan/splitsmith/commit/58b20faa82a98bc1b32de7daf4ae9224551dcf69))
* **hosted:** five more preflights downloaded raw sources to check existence ([#638](https://github.com/mandakan/splitsmith/issues/638)) ([#640](https://github.com/mandakan/splitsmith/issues/640)) ([c5d2dbd](https://github.com/mandakan/splitsmith/commit/c5d2dbd45a9e12c5720693214d589bed09a489a6))
* **hosted:** three preflights downloaded raw sources to check existence ([#637](https://github.com/mandakan/splitsmith/issues/637)) ([#639](https://github.com/mandakan/splitsmith/issues/639)) ([8b35747](https://github.com/mandakan/splitsmith/commit/8b357475885ba2a9b05fd860ec6ddbb19abcfdb5))


### Documentation

* **ui:** correct stale HostedUploadSurface docstring ([#523](https://github.com/mandakan/splitsmith/issues/523)) ([#633](https://github.com/mandakan/splitsmith/issues/633)) ([c1279f1](https://github.com/mandakan/splitsmith/commit/c1279f16d61adc97fe04bdd0a1f9112f5ab6bbf8))

## [0.13.0](https://github.com/mandakan/splitsmith/compare/v0.12.0...v0.13.0) (2026-08-03)


### Features

* audit-free trim export with per-shooter camera selection ([#612](https://github.com/mandakan/splitsmith/issues/612)) ([4933580](https://github.com/mandakan/splitsmith/commit/49335807911d4f25bf59b5c9645335030e70b6fa))
* **scripts:** staging magic-link minting script + /staging-login skill ([#606](https://github.com/mandakan/splitsmith/issues/606)) ([35c7c69](https://github.com/mandakan/splitsmith/commit/35c7c695749bce3e21c98e38637b80f4edf5db2b))
* **ui:** self-relative split tier labels (quick/typical/long) ([#604](https://github.com/mandakan/splitsmith/issues/604)) ([c9d58d9](https://github.com/mandakan/splitsmith/commit/c9d58d9362a968244552b8d948d7dd7e754682ef))


### Bug Fixes

* one audit precondition across every export surface ([#619](https://github.com/mandakan/splitsmith/issues/619)) ([#627](https://github.com/mandakan/splitsmith/issues/627)) ([7eb5626](https://github.com/mandakan/splitsmith/commit/7eb56264a111a53033d8b495f38e5a1c12415a42))
* one trim-eligibility rule across CLI, server and SPA ([#613](https://github.com/mandakan/splitsmith/issues/613), [#614](https://github.com/mandakan/splitsmith/issues/614)) ([#625](https://github.com/mandakan/splitsmith/issues/625)) ([1a8df09](https://github.com/mandakan/splitsmith/commit/1a8df09ca2dbb658a2340d265f35f4321b1e6363))
* surface trim-run divergence, guard camera ambiguity ([#617](https://github.com/mandakan/splitsmith/issues/617), [#618](https://github.com/mandakan/splitsmith/issues/618)) ([#626](https://github.com/mandakan/splitsmith/issues/626)) ([7b01927](https://github.com/mandakan/splitsmith/commit/7b019276f76cc1196dad4061f9c914f54a0e54f1))
* trim filename authority ([#615](https://github.com/mandakan/splitsmith/issues/615)) + honest trims-only export reporting ([#616](https://github.com/mandakan/splitsmith/issues/616)) ([#624](https://github.com/mandakan/splitsmith/issues/624)) ([6ea9dd7](https://github.com/mandakan/splitsmith/commit/6ea9dd76a005f74794da6f2b40e18f1ea45dfb74))
* **ui:** anomaly pins track waveform zoom + scroll ([#607](https://github.com/mandakan/splitsmith/issues/607)) ([c085c73](https://github.com/mandakan/splitsmith/commit/c085c73f3c40ca887ea50e6cd4f5ed79f2d53b44))
* **ui:** multi-cam per-stage export job crashed building its result ([#622](https://github.com/mandakan/splitsmith/issues/622)) ([05f5e2d](https://github.com/mandakan/splitsmith/commit/05f5e2d48b08b2618fc024c4b19c8595ed70dfe7))

## [0.12.0](https://github.com/mandakan/splitsmith/compare/v0.11.0...v0.12.0) (2026-07-08)


### Features

* **ui:** mobile results viewer -- shot ticker, fullscreen, sticky player ([#603](https://github.com/mandakan/splitsmith/issues/603)) ([041dbbf](https://github.com/mandakan/splitsmith/commit/041dbbfa26ba29338e3d095819f7d8523f47d9d9))


### Bug Fixes

* **ui:** label derived match HF + announce error banners to AT ([#601](https://github.com/mandakan/splitsmith/issues/601)) ([5710ed7](https://github.com/mandakan/splitsmith/commit/5710ed7514f545d8a7084a346c913638d7656182))

## [0.11.0](https://github.com/mandakan/splitsmith/compare/v0.10.6...v0.11.0) (2026-07-08)


### Features

* post-creation scoreboard linking + scorecard persistence (PR A) ([#599](https://github.com/mandakan/splitsmith/issues/599)) ([8311df3](https://github.com/mandakan/splitsmith/commit/8311df36223c1901d1302fc3f7e6ce22cc14cd87))
* **ui:** hide rejected shots by default with hold-to-peek ([#597](https://github.com/mandakan/splitsmith/issues/597)) ([c7bee6e](https://github.com/mandakan/splitsmith/commit/c7bee6ea59d79292d62bbb5f24068ba11c5a1369))
* **ui:** scored results view -- scorecard + splits + match totals (PR B) ([#600](https://github.com/mandakan/splitsmith/issues/600)) ([1422172](https://github.com/mandakan/splitsmith/commit/142217215cbf8c71004c747efbed60fc1dd09cc7))


### Bug Fixes

* derive stage status from state_docs in hosted mode ([#595](https://github.com/mandakan/splitsmith/issues/595)) ([6d609da](https://github.com/mandakan/splitsmith/commit/6d609da8710d489c5b402026504676448d0c0206))


### Documentation

* scoreboard linking + scored results (spec + PR A/B plans) ([#598](https://github.com/mandakan/splitsmith/issues/598)) ([b468bae](https://github.com/mandakan/splitsmith/commit/b468bae90d55e568d6f9cc0b0967856ebaf31249))

## [0.10.6](https://github.com/mandakan/splitsmith/compare/v0.10.5...v0.10.6) (2026-07-07)


### Bug Fixes

* serve audit /peaks + /audio without mirroring the full source ([#592](https://github.com/mandakan/splitsmith/issues/592)) ([#593](https://github.com/mandakan/splitsmith/issues/593)) ([8a17876](https://github.com/mandakan/splitsmith/commit/8a17876617fff458577a76e0a8ddaedcc442ec5d))

## [0.10.5](https://github.com/mandakan/splitsmith/compare/v0.10.4...v0.10.5) (2026-07-07)


### Bug Fixes

* serve preview media directly from R2 to fix slow hosted playback ([#589](https://github.com/mandakan/splitsmith/issues/589)) ([fa070cc](https://github.com/mandakan/splitsmith/commit/fa070cc91df537c13363c496de25e3fefe045df0))

## [0.10.4](https://github.com/mandakan/splitsmith/compare/v0.10.3...v0.10.4) (2026-07-07)


### Bug Fixes

* **ui:** make Space own play/pause over native video controls ([#587](https://github.com/mandakan/splitsmith/issues/587)) ([968e875](https://github.com/mandakan/splitsmith/commit/968e875c7a548d98dbddb75433f3b7bed5c63440))

## [0.10.3](https://github.com/mandakan/splitsmith/compare/v0.10.2...v0.10.3) (2026-07-07)


### Bug Fixes

* **ui:** show match name in Ingest breadcrumb instead of "..." ([#585](https://github.com/mandakan/splitsmith/issues/585)) ([fa0e80e](https://github.com/mandakan/splitsmith/commit/fa0e80e620d81dae25fe0cdb0e3d8e638e132651))
* **ui:** stop Space double-toggling a focused video player ([#586](https://github.com/mandakan/splitsmith/issues/586)) ([06dbd6e](https://github.com/mandakan/splitsmith/commit/06dbd6e215401a3f2036c788d0a8267066641701))


### Build / CI

* decouple prod deploy from the image publish ([#584](https://github.com/mandakan/splitsmith/issues/584)) ([e490fc5](https://github.com/mandakan/splitsmith/commit/e490fc5d52eb5095be76875a87a0aaf5c4a6df69))
* skip redundant :edge image build on release commits ([#582](https://github.com/mandakan/splitsmith/issues/582)) ([823dd7b](https://github.com/mandakan/splitsmith/commit/823dd7b4dbf35686cf2a1b7b6400eb00928a9c9d))

## [0.10.2](https://github.com/mandakan/splitsmith/compare/v0.10.1...v0.10.2) (2026-07-07)


### Bug Fixes

* stop blocking the event loop on stage assignment (async DB path) ([#578](https://github.com/mandakan/splitsmith/issues/578)) ([f123b33](https://github.com/mandakan/splitsmith/commit/f123b33ad9f48cd26d1a556d8afeb4fb66fd54a4))
* **ui:** drop redundant post-mutation refetches across the SPA ([#579](https://github.com/mandakan/splitsmith/issues/579)) ([571ea8c](https://github.com/mandakan/splitsmith/commit/571ea8cf06d5f5182035f9e11c0a29388ff2eab4))
* **ui:** optimistic stage assignment to kill click-to-update lag ([#576](https://github.com/mandakan/splitsmith/issues/576)) ([58640a8](https://github.com/mandakan/splitsmith/commit/58640a893836f0414bb8e2bc96660231150e59a4))

## [0.10.1](https://github.com/mandakan/splitsmith/compare/v0.10.0...v0.10.1) (2026-07-06)


### Bug Fixes

* worker priority direction so higher = preferred (matches UI copy) ([#574](https://github.com/mandakan/splitsmith/issues/574)) ([ebf28e8](https://github.com/mandakan/splitsmith/commit/ebf28e8cc09ba1df1bee04336f5e215b8c80fd7b))

## [0.10.0](https://github.com/mandakan/splitsmith/compare/v0.9.0...v0.10.0) (2026-07-06)


### Features

* log and display worker version in admin view ([#569](https://github.com/mandakan/splitsmith/issues/569)) ([d4644cc](https://github.com/mandakan/splitsmith/commit/d4644cc567aa744d1db9be5163dabe8a7fd1849e))
* **ui:** within-session background uploads ([#573](https://github.com/mandakan/splitsmith/issues/573)) ([6d53f35](https://github.com/mandakan/splitsmith/commit/6d53f35b27a683265214f9f939436c6b8ca05ad1))


### Bug Fixes

* auto-attach uploads, recover lost worker wakes ([#570](https://github.com/mandakan/splitsmith/issues/570)) ([#572](https://github.com/mandakan/splitsmith/issues/572)) ([b2abc35](https://github.com/mandakan/splitsmith/commit/b2abc35aec2a9ae7b54ce8b44d7b4699cc977ae8))

## [0.9.0](https://github.com/mandakan/splitsmith/compare/v0.8.4...v0.9.0) (2026-07-06)


### Features

* persistent LRU source cache for self-hosted worker agent ([#565](https://github.com/mandakan/splitsmith/issues/565)) ([#568](https://github.com/mandakan/splitsmith/issues/568)) ([7fe79dc](https://github.com/mandakan/splitsmith/commit/7fe79dce062849b96c7a17049c9d280a14334294))
* preview/proxy videos for uploads (fast stage-assignment streaming) ([#566](https://github.com/mandakan/splitsmith/issues/566)) ([b979d48](https://github.com/mandakan/splitsmith/commit/b979d48cdc9f50ac8ae976232d2d149bf47b2c9c))

## [0.8.4](https://github.com/mandakan/splitsmith/compare/v0.8.3...v0.8.4) (2026-07-05)


### Bug Fixes

* hosted ingest shooter scoping and video counts ([#563](https://github.com/mandakan/splitsmith/issues/563)) ([53cf27c](https://github.com/mandakan/splitsmith/commit/53cf27c5f3f087e24d148ea61e512d7d28bfd73d))

## [0.8.3](https://github.com/mandakan/splitsmith/compare/v0.8.2...v0.8.3) (2026-07-05)


### Bug Fixes

* **ui:** unfreeze hosted upload queue stuck at 0% ([#555](https://github.com/mandakan/splitsmith/issues/555)) ([2f2279c](https://github.com/mandakan/splitsmith/commit/2f2279c4baffa2d9d9aa1f9fc6dc743f29d8566f))

## [0.8.2](https://github.com/mandakan/splitsmith/compare/v0.8.1...v0.8.2) (2026-07-05)


### Bug Fixes

* **ui:** exempt /admin surfaces from the project-bind redirect ([#553](https://github.com/mandakan/splitsmith/issues/553)) ([574d334](https://github.com/mandakan/splitsmith/commit/574d334514c4751a10be1979cbbeda64c130ed74))

## [0.8.1](https://github.com/mandakan/splitsmith/compare/v0.8.0...v0.8.1) (2026-07-05)


### Bug Fixes

* **ui:** surface admin Workers link in AccountChip, not AppShell ([#551](https://github.com/mandakan/splitsmith/issues/551)) ([75837a0](https://github.com/mandakan/splitsmith/commit/75837a0939b0b6b5cb40d9280ed876c37877321e))


### Refactors

* **ui:** migrate legacy shadcn token aliases to Shot Timer tokens ([#552](https://github.com/mandakan/splitsmith/issues/552)) ([2eb4a66](https://github.com/mandakan/splitsmith/commit/2eb4a66a43094732aa5379adea71cad794df60e9))


### Build / CI

* run image publish and prod deploy automatically on release ([#548](https://github.com/mandakan/splitsmith/issues/548)) ([7044af6](https://github.com/mandakan/splitsmith/commit/7044af6703bce709c4a22e36c6931693e7f7e847))

## [0.8.0](https://github.com/mandakan/splitsmith/compare/v0.7.0...v0.8.0) (2026-07-05)


### Features

* public share links for match results (MVP) ([#541](https://github.com/mandakan/splitsmith/issues/541)) ([17ad330](https://github.com/mandakan/splitsmith/commit/17ad3301784884dbaa2419d1f867a1c082b8b1bf))
* self-hosted workers - registration, wake channel, priority dispatch ([#542](https://github.com/mandakan/splitsmith/issues/542)) ([d3fa93b](https://github.com/mandakan/splitsmith/commit/d3fa93b141d31439051cf016e7ed4ff25873da61))
* **ui:** copy-paste build/run/logs commands in worker register dialog ([#544](https://github.com/mandakan/splitsmith/issues/544)) ([31c4c5a](https://github.com/mandakan/splitsmith/commit/31c4c5ad72a15b69ef080a2c95d5d64733e4c648))


### Bug Fixes

* agent state dir /data writable by non-root container user ([#543](https://github.com/mandakan/splitsmith/issues/543)) ([8f80e2a](https://github.com/mandakan/splitsmith/commit/8f80e2af490020acdd1650f15b62af04a53188b4))
* **ui:** surface app version; drop inert help/settings buttons ([#538](https://github.com/mandakan/splitsmith/issues/538)) ([f14ecd2](https://github.com/mandakan/splitsmith/commit/f14ecd238290ddb97300d20355ca74ef4cbbaa40))


### Build / CI

* publish container image to GHCR (edge on main, semver on release) ([#545](https://github.com/mandakan/splitsmith/issues/545)) ([7b08069](https://github.com/mandakan/splitsmith/commit/7b0806973f85966d692a0f8768c5a37be07e7d9a))

## [0.7.0](https://github.com/mandakan/splitsmith/compare/v0.6.0...v0.7.0) (2026-07-04)


### Features

* **ui:** mobile results viewer - read-only match/stage playback + mobile shell ([#535](https://github.com/mandakan/splitsmith/issues/535)) ([164fd0c](https://github.com/mandakan/splitsmith/commit/164fd0cab79a6dbb51321027b7a8182f7ff00a4d))


### Bug Fixes

* **worker:** final-review hardening - awake-path net, worker gate, never-raise schedule ([#534](https://github.com/mandakan/splitsmith/issues/534)) ([1c4d861](https://github.com/mandakan/splitsmith/commit/1c4d8618206656b5a0468d3cceae4ccd020d1549))


### Build / CI

* 6-hourly wake-based worker safety net; document Railway cron incompatibility ([#533](https://github.com/mandakan/splitsmith/issues/533)) ([67979fa](https://github.com/mandakan/splitsmith/commit/67979fa9ec23b2c573506ea6e67cf7c080da9697))
* re-enable Railway auto-deploy (push -&gt; staging, release -&gt; production) ([#530](https://github.com/mandakan/splitsmith/issues/530)) ([ac99bb9](https://github.com/mandakan/splitsmith/commit/ac99bb9837f9ff4bcc2437231e0e9f6ae51e60e1))

## [0.6.0](https://github.com/mandakan/splitsmith/compare/v0.5.3...v0.6.0) (2026-07-03)


### Features

* **ingest:** two-pane master-detail redesign of Add Footage ([#513](https://github.com/mandakan/splitsmith/issues/513)) ([56e09ad](https://github.com/mandakan/splitsmith/commit/56e09ad90e8fa8838282a379be75445d1769cc13))
* **take:** multi-stage single-take videos - windowed beep detection + take overview ([#527](https://github.com/mandakan/splitsmith/issues/527)) ([bfa3be8](https://github.com/mandakan/splitsmith/commit/bfa3be80619897e4ed121477413a9995f81cb96f))
* **ui:** edit + verify camera mount/model on the ingest CameraCard ([#511](https://github.com/mandakan/splitsmith/issues/511)) ([4af81d5](https://github.com/mandakan/splitsmith/commit/4af81d51c165a7089a1a841c2f6327f5a2c7992c))
* **ui:** in-app stage reference on the ingest page ([#508](https://github.com/mandakan/splitsmith/issues/508)) ([f1a0437](https://github.com/mandakan/splitsmith/commit/f1a0437e730a781243a929f1c9b64ab643637002))
* **ui:** surface target shooter on ingest + move footage between shooters ([#510](https://github.com/mandakan/splitsmith/issues/510)) ([1ee0fb5](https://github.com/mandakan/splitsmith/commit/1ee0fb5ac369b9c4c30fba5cd784e5adb46a7ddb))


### Bug Fixes

* **audit:** waveform interaction batch - hit zones, peak-snap, region loop ([#526](https://github.com/mandakan/splitsmith/issues/526)) ([e9ccca4](https://github.com/mandakan/splitsmith/commit/e9ccca447b9a341edfc57f3be10e722cae9d9226))
* **beep:** make beep_reviewed the single source of truth + reopen confirmed beeps ([#518](https://github.com/mandakan/splitsmith/issues/518)) ([a0ac8b4](https://github.com/mandakan/splitsmith/commit/a0ac8b41969641bf9d1d72fbade672f07b9b32ff))
* **ingest:** address redesign review follow-ups ([#515](https://github.com/mandakan/splitsmith/issues/515)) ([a2de17b](https://github.com/mandakan/splitsmith/commit/a2de17b2e07a8b19bb90bc677dbc37144af223ab))
* **overview:** aggregate match dashboard instead of wrong-shooter scoping ([#517](https://github.com/mandakan/splitsmith/issues/517)) ([369a040](https://github.com/mandakan/splitsmith/commit/369a0405f721a5a45a73ac2c8e7d3a7f2994bf72))
* **ui:** clear all eslint errors, incl. pre-existing react-hooks violations ([#516](https://github.com/mandakan/splitsmith/issues/516)) ([0107a00](https://github.com/mandakan/splitsmith/commit/0107a00b1dc1d4400249a6a6ecf24b26aeebf489))
* **ui:** overlay layering architecture -- z tokens, body portals, dialog focus contract ([#519](https://github.com/mandakan/splitsmith/issues/519)) ([10d651e](https://github.com/mandakan/splitsmith/commit/10d651e0ded48eb8d36f27fa94343478c875706b))
* **ui:** remove dead first-run buttons, add phase-boundary CTAs, surface stage-time stall ([#520](https://github.com/mandakan/splitsmith/issues/520)) ([83a4de2](https://github.com/mandakan/splitsmith/commit/83a4de2728170ad31990a80dbdaf13c0f82d4188))


### Build / CI

* **deps:** re-lock with uv 0.11.25 ([#512](https://github.com/mandakan/splitsmith/issues/512)) ([3f2a726](https://github.com/mandakan/splitsmith/commit/3f2a7261d823fb332f96a257c7b8454b41c507d1))

## [0.5.3](https://github.com/mandakan/splitsmith/compare/v0.5.2...v0.5.3) (2026-06-24)


### Build / CI

* **deps:** Bump idna from 3.13 to 3.15 ([#504](https://github.com/mandakan/splitsmith/issues/504)) ([767dbe3](https://github.com/mandakan/splitsmith/commit/767dbe3928a0b4fbe3a8a603cff574c45cd7a430))
* **deps:** Bump react-router and react-router-dom ([#495](https://github.com/mandakan/splitsmith/issues/495)) ([2ffe9b5](https://github.com/mandakan/splitsmith/commit/2ffe9b563ecef887759517331d39f5615446a76a))
* **deps:** Bump urllib3 from 2.6.3 to 2.7.0 ([#505](https://github.com/mandakan/splitsmith/issues/505)) ([e8bd1ed](https://github.com/mandakan/splitsmith/commit/e8bd1edc8662fda21ab75de5bead6465005672c6))
* **deps:** clear Dependabot security alerts (Python + npm) ([#502](https://github.com/mandakan/splitsmith/issues/502)) ([8811417](https://github.com/mandakan/splitsmith/commit/881141741ad2f55a2d009c7c52cdba35b5220bb7))
* disable Railway auto-deploy on push/release ([#507](https://github.com/mandakan/splitsmith/issues/507)) ([a488383](https://github.com/mandakan/splitsmith/commit/a48838376711a0ab37407f1d19112ae259a782fa))

## [0.5.2](https://github.com/mandakan/splitsmith/compare/v0.5.1...v0.5.2) (2026-06-10)


### Bug Fixes

* **worker:** retry DB connect so a transient Neon PoolTimeout doesn't crash the drain ([#491](https://github.com/mandakan/splitsmith/issues/491)) ([22c1727](https://github.com/mandakan/splitsmith/commit/22c1727b28a704be22a6c977a615b66be0113d96))

## [0.5.1](https://github.com/mandakan/splitsmith/compare/v0.5.0...v0.5.1) (2026-06-08)


### Bug Fixes

* **worker:** size Procrastinate pool min_size=1 so cron drain survives Neon cold start ([#489](https://github.com/mandakan/splitsmith/issues/489)) ([4e8736b](https://github.com/mandakan/splitsmith/commit/4e8736b84f63ff7f498273ee277c81f55c047b31))

## [0.5.0](https://github.com/mandakan/splitsmith/compare/v0.4.0...v0.5.0) (2026-06-07)


### Features

* **saas:** hard-delete projects with full resource cleanup + confirm dialogs ([#486](https://github.com/mandakan/splitsmith/issues/486)) ([171f50f](https://github.com/mandakan/splitsmith/commit/171f50f356060fcea7d8835809eaa8e34edc6aea))
* **saas:** Tier-1 job observability -- per-phase timings, JSON logs, Sentry ([#485](https://github.com/mandakan/splitsmith/issues/485)) ([c1d19e2](https://github.com/mandakan/splitsmith/commit/c1d19e2b219bb5d6fdb6bfd964f7255bb20cbba1))
* **ui:** Ingest scan rows -- inline preview, assign feedback, role signposting ([#482](https://github.com/mandakan/splitsmith/issues/482)) ([760b574](https://github.com/mandakan/splitsmith/commit/760b5748751fdcdc2dbf2375324f40db6918c7bf))
* **ui:** resolve slug-less per-shooter URLs to the default shooter ([#479](https://github.com/mandakan/splitsmith/issues/479)) ([ff711f4](https://github.com/mandakan/splitsmith/commit/ff711f4a11c5f534c0955d3d56c45173b8b80d82))
* **worker:** add --one-shot drain mode so the DB compute can scale to zero ([#488](https://github.com/mandakan/splitsmith/issues/488)) ([1f59003](https://github.com/mandakan/splitsmith/commit/1f59003d0da63e82d798383e85297a702e19fca6))


### Bug Fixes

* **saas:** clean up shooter state_docs on removal ([#487](https://github.com/mandakan/splitsmith/issues/487)) ([fb5d80b](https://github.com/mandakan/splitsmith/commit/fb5d80bf236814c16c672689cde1f1dbbf7346c4))
* **saas:** populate match.stages on scoreboard-created matches ([#484](https://github.com/mandakan/splitsmith/issues/484)) ([fe5eb59](https://github.com/mandakan/splitsmith/commit/fe5eb59cc71f8fe3880e0ed3468c7c45ca845bbb))
* **ui:** keep the match prefix when Audit redirects to a stage ([#481](https://github.com/mandakan/splitsmith/issues/481)) ([7cf39e5](https://github.com/mandakan/splitsmith/commit/7cf39e5c53840e013a3bf42e9f9b8ca0b3e047ef))
* **ui:** make stage counts consistent across the overview ([#483](https://github.com/mandakan/splitsmith/issues/483)) ([c426909](https://github.com/mandakan/splitsmith/commit/c426909e7d00f3ad335b8d43ecb97fda8cb55938))

## [0.4.0](https://github.com/mandakan/splitsmith/compare/v0.3.0...v0.4.0) (2026-06-01)


### Features

* **auth:** introduce Auth abstraction with LoopbackAuth + GET /api/me ([#405](https://github.com/mandakan/splitsmith/issues/405)) ([a39b6ab](https://github.com/mandakan/splitsmith/commit/a39b6abf7dc27f1caa08769276a93dba414c1f1f))
* **compute:** introduce ComputeBackend abstraction with LocalComputeBackend ([#406](https://github.com/mandakan/splitsmith/issues/406)) ([17686a6](https://github.com/mandakan/splitsmith/commit/17686a653152d9070401f20b2c518439245fccb6))
* **db:** PostgresJobBackend (persistence-only) (doc 04) ([#420](https://github.com/mandakan/splitsmith/issues/420)) ([bf9b269](https://github.com/mandakan/splitsmith/commit/bf9b2695ad6001e0046cc64115c81da8e2dbef54))
* **db:** PostgresRecentProjectsStore + multi-tenant table pattern (doc 10) ([#417](https://github.com/mandakan/splitsmith/issues/417)) ([437aa3b](https://github.com/mandakan/splitsmith/commit/437aa3ba2948d7ca0c294731e02dc62d9d847114))
* **db:** PostgresScoreboardIdentityStore (doc 10) ([#419](https://github.com/mandakan/splitsmith/issues/419)) ([eb53691](https://github.com/mandakan/splitsmith/commit/eb53691903435eef0096e3c9a53f378366ac3503))
* **db:** SQLAlchemy 2.x + Alembic foundation with users table (doc 02) ([#416](https://github.com/mandakan/splitsmith/issues/416)) ([dc7f0b6](https://github.com/mandakan/splitsmith/commit/dc7f0b66d0cc9936aab263172c470e2861b1527b))
* **docker:** bake slim ONNX models into the hosted image ([#439](https://github.com/mandakan/splitsmith/issues/439)) ([dc42b78](https://github.com/mandakan/splitsmith/commit/dc42b785bb3ed14687406c9ee16a06e975d06787))
* **docker:** multi-stage slim image + hosted smoke script ([#441](https://github.com/mandakan/splitsmith/issues/441)) ([a84811f](https://github.com/mandakan/splitsmith/commit/a84811f27f489fa57622150578d725578471f8f4))
* **jobs:** introduce JobBackend Protocol (Tier 2 step 1) ([#413](https://github.com/mandakan/splitsmith/issues/413)) ([57711ac](https://github.com/mandakan/splitsmith/commit/57711ac41da4a7ee69104cfad18edb3597a93f60))
* **jobs:** kind+args dispatch + out-of-process worker (PR-gamma) ([#445](https://github.com/mandakan/splitsmith/issues/445)) ([2ea1a76](https://github.com/mandakan/splitsmith/commit/2ea1a76f0881685a9119c90e8ee7a2dfbd808168))
* **jobs:** worker runs detect_beep + shot_detect end-to-end (cross-process match resolution) ([#446](https://github.com/mandakan/splitsmith/issues/446)) ([1ac4d18](https://github.com/mandakan/splitsmith/commit/1ac4d1887691b3951dcbb55c7f88e81836633c88))
* presigned multipart upload for large raw videos ([#467](https://github.com/mandakan/splitsmith/issues/467)) ([#469](https://github.com/mandakan/splitsmith/issues/469)) ([64aa115](https://github.com/mandakan/splitsmith/commit/64aa115f8fef3faf4348296831b94753661bc17f))
* **saas:** audit-trim MP4 storage write-back (PR-epsilon part 1) ([#447](https://github.com/mandakan/splitsmith/issues/447)) ([f4be8ed](https://github.com/mandakan/splitsmith/commit/f4be8ede17a6a523bb21bbe32a3cf30f422cf489))
* **saas:** browser raw-video upload UX (hosted mode) ([#427](https://github.com/mandakan/splitsmith/issues/427)) ([a9e2056](https://github.com/mandakan/splitsmith/commit/a9e205632b00b03f63f2a8885e4a23cc9520bf6e))
* **saas:** database-enforced tenant isolation via Postgres RLS ([#450](https://github.com/mandakan/splitsmith/issues/450)) ([62833f7](https://github.com/mandakan/splitsmith/commit/62833f7fb5f33834dc2f6ee1439f574cf9cf4243))
* **saas:** docker-compose hosted-mode stack + splitsmith serve CLI ([#421](https://github.com/mandakan/splitsmith/issues/421)) ([a4d71b0](https://github.com/mandakan/splitsmith/commit/a4d71b067a8e9fa7ffd3bd5bdf5d80a2af33167e))
* **saas:** export media storage write-back (PR-epsilon part 2) ([#448](https://github.com/mandakan/splitsmith/issues/448)) ([47f3a95](https://github.com/mandakan/splitsmith/commit/47f3a95d62261183a74f3aa4b0d8fa2cf72e8ac9))
* **saas:** gate signups behind an allowlist toggle (anti-spam) ([#460](https://github.com/mandakan/splitsmith/issues/460)) ([3891aef](https://github.com/mandakan/splitsmith/commit/3891aefd9469ceca77931ca932f14d2deb9d50a4))
* **saas:** hosted-mode SPA cleanup ([#425](https://github.com/mandakan/splitsmith/issues/425)) ([#426](https://github.com/mandakan/splitsmith/issues/426)) ([33762a1](https://github.com/mandakan/splitsmith/commit/33762a16734c126ab04d6650bd583191b5dbbf2c))
* **saas:** in-house magic-link auth domain (auth-swap PR2a) ([#455](https://github.com/mandakan/splitsmith/issues/455)) ([005512a](https://github.com/mandakan/splitsmith/commit/005512a7ea66c3cb577092afc2d639c1c2310feb))
* **saas:** per-request/per-job tenant seam (auth-swap PR1) ([#451](https://github.com/mandakan/splitsmith/issues/451)) ([b1c425a](https://github.com/mandakan/splitsmith/commit/b1c425a5bf1831ceb4700aa089e135f208ca6a1d))
* **saas:** POST /api/shooters/{slug}/raw-videos/attach endpoint ([#433](https://github.com/mandakan/splitsmith/issues/433)) ([49514f1](https://github.com/mandakan/splitsmith/commit/49514f12c5c1810d2a64d2df33a40ac813f58b8f))
* **saas:** Procrastinate queue foundation (PR-alpha) ([#437](https://github.com/mandakan/splitsmith/issues/437)) ([ffda233](https://github.com/mandakan/splitsmith/commit/ffda233ed09b460b4ee5353d7d09c50d542fffdc))
* **saas:** raw_videos[] on MatchProject + v2-&gt;v3 migration (doc 05) ([#428](https://github.com/mandakan/splitsmith/issues/428)) ([9db77bc](https://github.com/mandakan/splitsmith/commit/9db77bcf21abfbe62c055a58067df48ba41ff08d))
* **saas:** Resend email sender for production magic-link delivery ([#456](https://github.com/mandakan/splitsmith/issues/456)) ([f3ea7b9](https://github.com/mandakan/splitsmith/commit/f3ea7b9e11818a897a8371ee0f7e82d63aff74fc))
* **saas:** SPA "attach to project" action in HostedUploadSurface ([#435](https://github.com/mandakan/splitsmith/issues/435)) ([150d5f7](https://github.com/mandakan/splitsmith/commit/150d5f78ddf2ed6e8b5df0b89888b1794d89756c))
* **saas:** SPA magic-link login surface (auth-swap PR2c) ([#454](https://github.com/mandakan/splitsmith/issues/454)) ([e2dfd02](https://github.com/mandakan/splitsmith/commit/e2dfd020d5c9eef7840cce2e468494d58e9168a8))
* **saas:** splitsmith worker CLI + compose worker service (PR-beta) ([#443](https://github.com/mandakan/splitsmith/issues/443)) ([ba67060](https://github.com/mandakan/splitsmith/commit/ba67060820be7c38797d410c0504e46985137436))
* **saas:** storage-aware resolve_video_path + worker download cache ([#434](https://github.com/mandakan/splitsmith/issues/434)) ([178a998](https://github.com/mandakan/splitsmith/commit/178a998aab9dcd27274373e6d5c045623ae95dd7))
* **saas:** swap magic-link email transport Resend -&gt; Lettermint ([#458](https://github.com/mandakan/splitsmith/issues/458)) ([64a9fc9](https://github.com/mandakan/splitsmith/commit/64a9fc9cbaaa17f206d68af715e7f1f6057595a5))
* **saas:** wire MagicLinkAuth + login routes; retire HostedLoopbackAuth (auth-swap PR2b) ([#453](https://github.com/mandakan/splitsmith/issues/453)) ([5c478e1](https://github.com/mandakan/splitsmith/commit/5c478e1be16e4476affef2e089e847551b6fd12c))
* **saas:** wire S3Storage in hosted mode + raw upload endpoint ([#424](https://github.com/mandakan/splitsmith/issues/424)) ([a68e00c](https://github.com/mandakan/splitsmith/commit/a68e00c6ef97664cd3f02ed614246226d55365e6))
* **saas:** worker pushes extracted audio to S3 (Phase 1) ([#436](https://github.com/mandakan/splitsmith/issues/436)) ([4dab5aa](https://github.com/mandakan/splitsmith/commit/4dab5aaf1b4a561f10efd8a3e331db72e7a3ee09))
* **storage:** introduce Storage abstraction with FilesystemStorage ([#407](https://github.com/mandakan/splitsmith/issues/407)) ([f16d672](https://github.com/mandakan/splitsmith/commit/f16d67272cb4bc386abf3a67e66846c7901f30b7))
* **storage:** open_stream for chunked reads on both backends ([#429](https://github.com/mandakan/splitsmith/issues/429)) ([e0d32bb](https://github.com/mandakan/splitsmith/commit/e0d32bb7919c9a79e5d4b8d9f236f06509a0c99e))
* **storage:** S3Storage backend wrapping boto3 (R2 / S3 / minio) ([#415](https://github.com/mandakan/splitsmith/issues/415)) ([f5931af](https://github.com/mandakan/splitsmith/commit/f5931af210e226c05731402d0f98d09e90d6f1fb))
* Tier 3 (user preferences) + Tier 4 (CLI URL emission) - singleton elimination complete ([#414](https://github.com/mandakan/splitsmith/issues/414)) ([4685a96](https://github.com/mandakan/splitsmith/commit/4685a96f91a92fdd05cbb6c96638a02abbfa07f2))
* **ui:** auto-prefetch slim models on UI launch ([#404](https://github.com/mandakan/splitsmith/issues/404)) ([8a27696](https://github.com/mandakan/splitsmith/commit/8a27696bed8a9ad0c79dd27fbbbafc213c99b42c))


### Bug Fixes

* **db:** apply procrastinate schema statement-by-statement (asyncpg) ([#440](https://github.com/mandakan/splitsmith/issues/440)) ([d153bd9](https://github.com/mandakan/splitsmith/commit/d153bd9798f5f83ea6c3ea25527ec22b6256ae17))
* **hosted:** NullPool for asyncpg loop-binding + HOSTED-LOCAL docs ([#423](https://github.com/mandakan/splitsmith/issues/423)) ([eaebdc9](https://github.com/mandakan/splitsmith/commit/eaebdc924b24154bdbc4d6700ea9d754222a4996))
* **hosted:** picker detail from store + default-select a shooter for per-shooter tabs ([#477](https://github.com/mandakan/splitsmith/issues/477)) ([f91c498](https://github.com/mandakan/splitsmith/commit/f91c498fb070911b8bcc7cd150f6afd1749f3f48))
* **hosted:** playback media-URL scoping + export overview/result write-back ([#472](https://github.com/mandakan/splitsmith/issues/472), [#8](https://github.com/mandakan/splitsmith/issues/8)) ([#473](https://github.com/mandakan/splitsmith/issues/473)) ([d75110e](https://github.com/mandakan/splitsmith/commit/d75110e4844fa3312694f1509544c66bc6e194ab))
* **queue:** pass conninfo directly to PsycopgConnector + worker round-trip smoke ([#444](https://github.com/mandakan/splitsmith/issues/444)) ([2a284cd](https://github.com/mandakan/splitsmith/commit/2a284cde6853cbb24bc7b16938b4d0a26f4fbc10))
* **saas:** hosted picker/bind resolves a match by match_id, not ephemeral path ([#475](https://github.com/mandakan/splitsmith/issues/475)) ([2ea9e9b](https://github.com/mandakan/splitsmith/commit/2ea9e9bc4631b06dea97ebdaa2e114d94b01f3ea))
* **serve:** feed engine StageData a sentinel scorecard time on export ([#471](https://github.com/mandakan/splitsmith/issues/471)) ([4a69614](https://github.com/mandakan/splitsmith/commit/4a6961401f9573862e9dcc6824046c47277bc249))
* **serve:** honor a "Mark reviewed" that lands mid-trim so shot-detect chains ([#478](https://github.com/mandakan/splitsmith/issues/478)) ([f713d82](https://github.com/mandakan/splitsmith/commit/f713d822571bd6229f792ae672bfa2bfda91d7d1))
* **serve:** hosted match state in Postgres (state refactor phases 1-2) ([#465](https://github.com/mandakan/splitsmith/issues/465)) ([a78ccbd](https://github.com/mandakan/splitsmith/commit/a78ccbdff217c98b2913d9b4ee3a66b72717155d))
* **serve:** let manually-timed stages export (manual matches were blocked) ([#470](https://github.com/mandakan/splitsmith/issues/470)) ([c0de9c4](https://github.com/mandakan/splitsmith/commit/c0de9c4b2769f1cffc41c3cf473394e420950d73))
* **serve:** surface splitsmith.* INFO logs (incl. console magic link) on stdout ([#466](https://github.com/mandakan/splitsmith/issues/466)) ([03fc6d6](https://github.com/mandakan/splitsmith/commit/03fc6d604971fa067335946b81f008c3209ebf01))
* **ui:** beep review plays the source video, not the cached trim ([#474](https://github.com/mandakan/splitsmith/issues/474)) ([df1c48e](https://github.com/mandakan/splitsmith/commit/df1c48e1d7012c8c6b6caac2068f77529689773b))
* **ui:** don't block graceful shutdown on the model prefetch ([#438](https://github.com/mandakan/splitsmith/issues/438)) ([be53a35](https://github.com/mandakan/splitsmith/commit/be53a35e76b1ab3cbfb65e7430e16a7aeedb2e37))
* **ui:** explain why per-shooter sections route to the shooter list ([#476](https://github.com/mandakan/splitsmith/issues/476)) ([a4cd589](https://github.com/mandakan/splitsmith/commit/a4cd5890b991500b06f557a6de57c94b0f084541))
* **upload:** drop blocking client-side hash; stream straight to upload ([#464](https://github.com/mandakan/splitsmith/issues/464)) ([317e469](https://github.com/mandakan/splitsmith/commit/317e46906afeec60bb2c855ad080ff39b621164e))


### Refactors

* **serve:** finish state refactor -- drop dead JSON-mirror code + worker re-merge retry (phases 3+4) ([#468](https://github.com/mandakan/splitsmith/issues/468)) ([1ea3342](https://github.com/mandakan/splitsmith/commit/1ea3342c014fad1d42b668497c8fbb1fedb50e40))
* **state:** match_root reads only the per-request ContextVar (Tier 1 step) ([#409](https://github.com/mandakan/splitsmith/issues/409)) ([cc135af](https://github.com/mandakan/splitsmith/commit/cc135af33e98d88e652dcadb52eccce7be58359f))
* **state:** retire legacy single-shooter projects (Tier 1 step 3) ([#411](https://github.com/mandakan/splitsmith/issues/411)) ([148e150](https://github.com/mandakan/splitsmith/commit/148e15083232a59d8d77cfea6b934edc82594d03))
* **state:** retire the bound singleton entirely (Tier 1 step 4) ([#412](https://github.com/mandakan/splitsmith/issues/412)) ([c0a07a4](https://github.com/mandakan/splitsmith/commit/c0a07a44f67001655228989e91674ece80f52bf8))
* **state:** shooter_root drops the Match-folder singleton fallback (Tier 1 step 2) ([#410](https://github.com/mandakan/splitsmith/issues/410)) ([090ffd3](https://github.com/mandakan/splitsmith/commit/090ffd30644219655588474bff34dd951b49f73f))


### Documentation

* **saas:** add singleton elimination map (doc 10) ([#408](https://github.com/mandakan/splitsmith/issues/408)) ([e2450ff](https://github.com/mandakan/splitsmith/commit/e2450ff080f77ee7dc3e15902a6d24a5ca1be490))
* **saas:** environment strategy (staging + prod) across providers ([#459](https://github.com/mandakan/splitsmith/issues/459)) ([e008722](https://github.com/mandakan/splitsmith/commit/e00872222513ade0780c765027f010b54665ff55))
* **site:** lead Quickstart with `splitsmith ui` from PyPI ([#402](https://github.com/mandakan/splitsmith/issues/402)) ([d5d48f9](https://github.com/mandakan/splitsmith/commit/d5d48f9519ce8d4205068415489c54780159741c))


### Build / CI

* bump actions/checkout v4 -&gt; v5 across workflows ([#463](https://github.com/mandakan/splitsmith/issues/463)) ([7603d2e](https://github.com/mandakan/splitsmith/commit/7603d2ef98dfd4ab54af914506f10d3324f8d8f8))
* **deploy:** Railway deploy workflow, .railwayignore, and Procrastinate DSN fix ([#461](https://github.com/mandakan/splitsmith/issues/461)) ([271d2ee](https://github.com/mandakan/splitsmith/commit/271d2ee70b7c0ff1a40ce5e66f92f4e3f77193e3))
* **docker:** build the SPA inside the image (Node stage) ([#457](https://github.com/mandakan/splitsmith/issues/457)) ([68dc46a](https://github.com/mandakan/splitsmith/commit/68dc46accc74ddda8acd8fbefed6609170a199ce))

## [0.3.0](https://github.com/mandakan/splitsmith/compare/v0.2.1...v0.3.0) (2026-05-25)


### Features

* **beep-review:** single home for beep work; trim audit page ([#399](https://github.com/mandakan/splitsmith/issues/399)) ([9ecf999](https://github.com/mandakan/splitsmith/commit/9ecf9998f94edeb5420e3a982f97ae4edb2114f0))
* **brand:** hero + og:image, new tagline, audit shortcut hints ([#401](https://github.com/mandakan/splitsmith/issues/401)) ([40aa55a](https://github.com/mandakan/splitsmith/commit/40aa55ac56da0e9c81ae373554b9dc8816f4b2e0))

## [0.2.1](https://github.com/mandakan/splitsmith/compare/v0.2.0...v0.2.1) (2026-05-24)


### Bug Fixes

* **docs:** use absolute GitHub URLs for README images on PyPI ([#398](https://github.com/mandakan/splitsmith/issues/398)) ([de435a6](https://github.com/mandakan/splitsmith/commit/de435a6981b0532f05c159a73da86a9d107bf6af))

## 0.2.0 (2026-05-24)

First public release.

Extract IPSC shot splits from head-mounted camera footage. Detect shots
via a 3-voter ensemble (envelope onset / CLAP / GBDT-with-PANN), produce
a CSV of splits, and emit an FCPXML timeline with per-shot markers and
optional overlay clips for Final Cut Pro.

Install:

```
uv tool install splitsmith
```

After install, run `splitsmith fetch-models` to pre-download the ~440 MB
of ONNX detection artifacts (otherwise they download on first detection).
