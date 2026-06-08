# Changelog

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
