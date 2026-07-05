# Changelog

## [0.8.1](https://github.com/mandakan/splitsmith/compare/v0.8.0...v0.8.1) (2026-07-05)


### Bug Fixes

* **ui:** surface admin Workers link in AccountChip, not AppShell ([#551](https://github.com/mandakan/splitsmith/issues/551)) ([75837a0](https://github.com/mandakan/splitsmith/commit/75837a0939b0b6b5cb40d9280ed876c37877321e))


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
