# Independent iMessage text bridge

No EFB process or Telegram configuration is changed. The Mac worker uses only
loopback HTTP to BlueBubbles and a password-authenticated, existing-host-key
verified SSH PTY for the independent NAS stdio gateway. No listener is added.

Private runtime: `~/Library/Application Support/imessage-text-bridge` (0700).
Private config, allowlist, state and locks: 0600. Credentials are obtained in
memory through the existing project credential helper. BlueBubbles password is
read from its local config database. No message bodies are stored in the ledger,
and worker stdout/stderr are discarded. The project source contains no private
identifiers. No launch agent/login item is installed.

The three `.command` controls invoke `on`, `off` and `status`. Initial state is
closed. Each enable first confirms NAS off, then obtains a fresh epoch and starts
at the current time. A failed remote close is recorded locally and must be
reconciled before a subsequent enable. Off commits the local fence immediately;
an already in-flight send cannot be recalled. The control waits for any such
request before confirming the NAS source off/cancel operation.

The worker owns a nonblocking OS file lock. An independent emission lock and
local state checks fence sends across off/on races. Every actual send has a
persistent intent first. A timeout or process exit makes it unknown and it is
never retried. IDs are persisted without text. BlueBubbles' temporary send cache
is not treated as durable delivery confirmation. `submitted` means a request was
accepted, not that either person received it.

The SSH PTY disables canonical buffering for this connection only. Requests use
ASCII Base64 framing over encrypted SSH, decoded to a single UTF-8 JSON line
before the gateway. Responses are normalized to ASCII JSON to preserve Unicode
through Expect. Decoded requests are limited to 19,900 bytes. Host selection is
pinned to the private expected host digest and StrictHostKeyChecking=yes.

Only the exact locally validated, single-person iMessage chat GUID is queried.
Own messages, reactions, system/group events, attachments and non-text payloads
are excluded. Queries use a fixed time window, inclusive boundaries plus GUID
ledger deduplication. More than 100 items in a window fails closed instead of
silently advancing. Initial start and every process restart begin at now. A
connection failure pauses work; recovery discards that interval. Messages older
than 120 seconds are rejected. The NAS gateway also enforces the same age
limit and supplies `{id,text,created}` with the original log timestamp as Unix
seconds. Missing, expired or future timestamps are rejected. Age is checked again
after taking the emission lock, immediately before the send intent. No local
offline message backlog is kept.

Run mock tests: `python3 -m unittest discover -s imessage-text-bridge -v`.
No test invokes a real messaging endpoint. The only live checks allowed during
installation are the gateway `status`/`off` operations and exact allowlist chat
metadata resolution; there are no test sends.

BlueBubbles itself is the unmodified official app and still listens on all
interfaces at its configured port. This worker accesses it via loopback only;
it does not change that app's binding or the system firewall.
