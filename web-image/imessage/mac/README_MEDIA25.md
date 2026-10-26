25-MiB media release — coordinated code-only activation

Payload cap: 25 * 1024 * 1024 = 26,214,400 bytes, inclusive. JPEG/PNG, ordinary documents and MP4 at exactly this size are accepted by the existing validators; one extra byte is rejected before transport. image_api.MAX_BYTES is the common source used by attachment validation, ImageTransfer and both incoming image/file filters in bridge.py. Existing MIME/signature/filename/whitelist rules are unchanged.

The Expect response-spool bound was 20 MiB and could not hold Base64-framed 25-MiB payloads. ssh_images.exp now permits 50 MiB of framed output, matching Python MAX_BYTES * 2. Each JSON frame remains bounded to 19,900 bytes and each decoded chunk to 8192 bytes. Private 0600 spools are still removed after transfers. The 75-second monotonic transport deadline, 120-second message age, five-pending NAS queue, off/epoch/revision fencing, independent attachment floor and persistent unknown-result dedupe are unchanged. No timeout was extended and no service was added.

Working-project tests: 164 passed. This public bundle runs 156 tests; the eight prior image/managed activation-helper tests are excluded because those older helpers are not in this bundle. New test_media25_boundaries.py covers exactly 25 MiB and 25 MiB + 1 for JPEG, PNG, generic files and MP4, plus incoming metadata and pre-network rejection.

Explicit verify_media25_stream_mock.py was run over the existing protected SSH transport against standalone stateless remote Python simulators. It transfers deterministic synthetic signature payloads, not private user files, and never invokes gateway/image_pipe, EFB, Telegram, WeChat or Messages. All eight 25-MiB put/get probes matched exact bytes/digests and cleaned temporary spools:
- JPEG: put 10.926 seconds; get 7.634 seconds.
- PNG: put 11.082 seconds; get 6.988 seconds.
- PDF/file: put 10.725 seconds; get 10.697 seconds.
- MP4: put 19.425 seconds; get 7.029 seconds.
The slowest observed operation was 19.425 seconds. These are current-path measurements, not a guarantee under arbitrary load. They support retaining the 75-second deadline. Full record: STREAM_VERIFICATION.json.

This proves transport capacity and local size boundaries, not actual 25-MiB app delivery or media decoding/playback. No real test message/file was sent or old event replayed. NAS 25-MiB limits and Telegram image-to-document handling must be coordinated by the primary task before deployment. Never run an activation helper solely because this candidate is present. Back up private code/config first, restart only the independent Mac worker with preserved text epoch/start/cursor, keep the current ledger and retain added unused modules during rollback. Do not change current desired settings or add a launch service.

activate_media25.py defaults to read-only preflight. --activate requires both NAS runtime constants to equal 25 MiB and the image backend to be ready, then backs up private code/config, replaces only changed runtime files, preserves the config byte-for-byte, text epoch/start/cursor and ledger, advances image/file floors to now, and restarts only the independent worker. It verifies three fresh acknowledgements, a single worker, runtime constants and unchanged configuration. On failure it restores only prior Mac code, retains config/ledger and unused modules, and starts the independent worker with preserved text state.
