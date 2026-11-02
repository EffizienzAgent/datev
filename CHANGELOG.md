# Changelog

## [0.1.0](https://github.com/EffizienzAgent/datev/compare/v0.1.0...v0.1.0) (2026-11-02)


### Features

* add booking intake, batching, approval and dispatch ([1ae9215](https://github.com/EffizienzAgent/datev/commit/1ae921507c1d0eb72aa13591671909938a964254))
* add Business OS and DUo e-mail storage connectors ([5814a36](https://github.com/EffizienzAgent/datev/commit/5814a36615aaf58068fa5be27adedc4f014034b3))
* add Business OS API client ([708c564](https://github.com/EffizienzAgent/datev/commit/708c564ff6089fcf0caeaaac1375db08d44ab2e0))
* add DATEV Client with connector, dispatch and account settings ([3e17e9c](https://github.com/EffizienzAgent/datev/commit/3e17e9c9aedd8a5fe84b686f104bf9e78b5f6d74))
* add DATEVconnect and EXTF export and master data connectors ([81cb678](https://github.com/EffizienzAgent/datev/commit/81cb678f17c5f8880f4ef56e4f0a53dcd580f766))
* add DATEVconnect API client ([6f6802b](https://github.com/EffizienzAgent/datev/commit/6f6802b419ad39e8979fcce3916f4ea72d3f0a54))
* add document intake and upload pipeline with backoff ([8f6f137](https://github.com/EffizienzAgent/datev/commit/8f6f1376a1e2fd0c92c9d295249ce29654e5de05))
* add document, booking, batch, party account and event log doctypes ([dc485dc](https://github.com/EffizienzAgent/datev/commit/dc485dcb04b4eb7502e9063afaf07065096e2075))
* add EXTF writer for booking batches and party master data ([f60b6fd](https://github.com/EffizienzAgent/datev/commit/f60b6fdcd5ac8fce08ac8efacd6dd4ee6ea232e8))
* add http wrapper and connector registry ([aa884fb](https://github.com/EffizienzAgent/datev/commit/aa884fb8897e55130609e78f1dc6602cfc34f05c))
* add intake HTTP API ([8742af3](https://github.com/EffizienzAgent/datev/commit/8742af391bfc7d792a6b6a5b44c84e94d11f2195))
* add party accounts with numbering, DATEV sync and initial matching ([dffbc4c](https://github.com/EffizienzAgent/datev/commit/dffbc4c50f6ef9099f270302d0ed00d9f45d2db1))
* add status report, workspace and notifications ([f4e8775](https://github.com/EffizienzAgent/datev/commit/f4e87758d72bc2dd87b3d1f5fefc00391738871b))
* demo data and screenshot script ([ef048ce](https://github.com/EffizienzAgent/datev/commit/ef048ce90885bd0e555a70da4594d1e490256835))
* resolve document types per client ([ecda328](https://github.com/EffizienzAgent/datev/commit/ecda3288b38af3fb7afaa9668fa949fe5c3fc80e))


### Bug Fixes

* count failed bookings and party errors in status and overdue checks ([d4f0d19](https://github.com/EffizienzAgent/datev/commit/d4f0d1998ee084f33ec42f096d222fbe7f3340bb))
* guard discard and bookings on discarded documents, keep failures on resolve ([0853b58](https://github.com/EffizienzAgent/datev/commit/0853b5852ef134f3fb8c0f551d16d2fa2c1931a8))
* hold uncertain DATEV deliveries for manual resolution and tighten dispatch edges ([ed9863e](https://github.com/EffizienzAgent/datev/commit/ed9863ee29aa4dee076f39d6ae01852b4757b81f))
* isolate per-document errors in retry_due and lock before discard ([e2a249e](https://github.com/EffizienzAgent/datev/commit/e2a249e28959a3c124822420ee5c4f275be0b3e0))
* isolate unbuildable proposals, send date-time and enrich party payload ([3233e74](https://github.com/EffizienzAgent/datev/commit/3233e7414731e030833c9f3a32bc05bb8047385e))
* keep certain results when one DATEVconnect group is uncertain and widen maybe-sent detection ([d495370](https://github.com/EffizienzAgent/datev/commit/d495370666601c9c72e2329e049e17d0991bf91a))
* keep remote party creation, retry party errors, check EXTF imports ([023424f](https://github.com/EffizienzAgent/datev/commit/023424ff5129d19720fc054a5878ef5f789aa8df))
* persist upload results first and never re-upload uncertain documents ([cb72beb](https://github.com/EffizienzAgent/datev/commit/cb72beb81e82ed90e1753d6c4fbdc0cb2371006d))
* preflight DATEVconnect fiscal years and payments ([4d4b938](https://github.com/EffizienzAgent/datev/commit/4d4b9381ec071ece310904dfcc500540baf29e4b))
* protect status and link fields, add reset_booking ([e3a6f90](https://github.com/EffizienzAgent/datev/commit/e3a6f90b104b2582d8f55e99589225ef2f85b530))
* query Business OS companies per connection and flag suggested connection ([7e71853](https://github.com/EffizienzAgent/datev/commit/7e71853b3e94b607bcffbe847341d52936c772f2))
* raise DATEVconnectError for malformed party rows ([1013279](https://github.com/EffizienzAgent/datev/commit/1013279ab08ba96908cb44f156184633076e0437))
* reject non-finite numbers and cross-client source keys in intake ([eab4f17](https://github.com/EffizienzAgent/datev/commit/eab4f178eef35626a5d9f36c531e927688277c84))
* require bookings to use a party account of the same client and kind ([8b0bd8c](https://github.com/EffizienzAgent/datev/commit/8b0bd8c5ef0665d33cabbfdc22a1dc629b4a2bf5))
* send competing party matches to clarification and require numbers for settled accounts ([2517a39](https://github.com/EffizienzAgent/datev/commit/2517a39af7a52037b9cfc6375aae644d81c4630b))
* treat unmappable DATEVconnect responses as uncertain ([d939308](https://github.com/EffizienzAgent/datev/commit/d9393081e4e02774d81fccc040a6e681ffcffded))
* write one EXTF master data file per fiscal year of the batch ([fb85914](https://github.com/EffizienzAgent/datev/commit/fb85914ed8b287abed88bb6dc4ce14dfde613b4d))


### Documentation

* add German translations, README and docs ([7accd15](https://github.com/EffizienzAgent/datev/commit/7accd156ef7922b1b21ec663bde02a0c2d9324d9))
* describe reset, retry, queued sends, EXTF import check and uncertainty rules ([3d3adb8](https://github.com/EffizienzAgent/datev/commit/3d3adb813aece96ac1eab4b923ccddd6d1b746c5))
* format integration guide examples ([5c954e8](https://github.com/EffizienzAgent/datev/commit/5c954e888c787021590aebd1333017dfd65e77af))
* integration guide, work-in-progress notice and screenshots ([a45e4af](https://github.com/EffizienzAgent/datev/commit/a45e4afb4c4ce328f3c1b8a78df81bb107eb8f2a))


### Miscellaneous Chores

* release 0.1.0 ([1699559](https://github.com/EffizienzAgent/datev/commit/1699559ea99d056a74e16c856b2367d8013ab807))
