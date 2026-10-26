# Image feature: staged, disabled

The running text bridge is unchanged. New Mac source enables image processing only
when private config `images_enabled` is explicitly true and NAS status has
`images_ready:true`. Poll then opts in with `images:true`. A new image floor is set
when readiness first becomes available; no earlier photos are backfilled.

Supported wire formats: JPEG and PNG, <=10 MiB per attachment. Incoming HEIC/HEIF
may be converted by BlueBubbles' ordinary download endpoint (`original=false`).
GIFs, stickers, videos, Live Photo video parts and image captions are not relayed
as separate text. No image from another chat is queried/downloaded. The existing
single-person exact chat GUID remains the sole whitelist.

BlueBubbles 1.9.9 APIs verified against its installed bundle and versioned source:

- GET `attachment/{guid}/download?original=false&force=false`, authenticated locally.
- POST `message/attachment`, multipart fields `chatGuid,tempGuid,name,method` and
  file part `attachment`; `method=apple-script`. Filename is generated from an
  event hash, never the original sender's filename.

The existing Messages Automation authorization is used; no Private API, SIP
change, Accessibility grant or new login is required by these code paths.
Reading the actual attachment file still requires BlueBubbles' normal filesystem
access. No old images have been downloaded to claim that runtime validation.

`image_transfer.py` uses one existing-host-key-verified SSH connection per image,
with JSON/Base64 lines in private 0600 spool files inside 0700 temporary folders.
Image bytes are never placed in argv, environment, process stdout or logs. The
75-second transfer deadline and local-off callback terminate only that SSH process
group. Complete data, offsets, count, MIME and SHA256 must match. Spools are removed
on success or error. The NAS wire contract is in `images/PROTOCOL.md`.

Image failures do not disable text or retry unknown sends. A not-yet-downloaded
local attachment can be read again while fresh, before any send intent is created.
Once upload/send intent exists, its ID is never re-emitted, including after a
restart. Every restart/reconnect and readiness transition rejects older images.

Validation completed: 48 offline tests, plus a synthetic 2 MiB upload and download
over one SSH connection each. These mocks do not call gateway, EFB, Telegram or
Messages. No live image send or historical image read has happened.

NAS `image_pipe.py` and ready/poll integration must be finalized before deploying
this source and enabling the private flag. Restart only the independent Mac bridge
from now when activation is authorized; do not restore an older ledger.
