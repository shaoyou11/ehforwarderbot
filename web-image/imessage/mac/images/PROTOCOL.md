# Mac image stream contract (proposal for NAS image_pipe.py)

Text gateway and its JSON protocol remain unchanged. No image body goes through
an environment variable, argv, the text gateway's 19,900-byte request, or a public
listener. One image transfer uses one existing-host-key-verified SSH connection.
JPEG/PNG only, maximum 10 MiB; original Unix-second `created` is mandatory, age
0..120 seconds. Requests are restricted to the existing source/route/epoch.

Command: `python3 -u image_pipe.py CONFIG put` or `... CONFIG get`.
After authentication, server emits exactly `IMESSAGE_STDIN_READY\n`.
Client sends one compact ASCII JSON header (under 2 KiB) plus newline.

## put

Header: `{epoch,event_id,size,digest,mime,created}`.
Server checks source enabled, epoch, binding, image enable, original age, queue
capacity, ImageStore.begin, and emits `IMESSAGE_IMAGE_BODY_READY\n` only when a new
upload should proceed. Otherwise emit one final JSON result and exit, without
requesting body. Client then streams from a local private spool file:

- `{offset,data}` per line, `data` strict Base64, decoded chunks <=8192 bytes.
- Last line `{op:"commit"}`.

Server bounds each input line <=19,900 bytes, total decoded <=10 MiB, verifies
complete contiguous data and digest/format, then commits at most once. Final
JSON `{status:"pending",id}` or `{error:CODE}`. Duplicate/non-uploading replies
must not start a new upload. Never replay a send after an uncertain reply.

## get

Header: `{epoch,image_id}`; opaque image_id only, never a caller-supplied path.
Server checks the image belongs to the exact consumed white-listed event and is
fresh/current epoch. Output is ASCII JSON lines:

1. `{kind:"image",size,digest,mime,created}`
2. `{offset,data}` chunks as above
3. `{status:"complete"}`

The client spools stdout privately, verifies count, offsets, digest and image
format, then marks a durable send intent before local BlueBubbles multipart send.
Server must not return images from an arbitrary ID, other account or old epoch.

## Existing gateway

- status additionally returns `images_ready:true` only after NAS integration is ready.
- poll adds optional request `images:true`; only this opt-in may consume Image rows.
- image poll result: `{enabled,epoch,message:{id,kind:"image",image_id,size,digest,mime,created}}`.
- text poll schema is unchanged.
- off cancels this source's image uploading/pending items, as well as text.

The new Mac feature is disabled unless its private config has images_enabled=true.
Polling opts in only when this flag and images_ready are both true. Existing text
mode never consumes images. There is no automatic retry of unknown image sends,
no old-history replay, no automatic launch, and no new system permission request.
