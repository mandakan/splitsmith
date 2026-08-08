# Changelog

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
