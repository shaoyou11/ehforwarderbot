# EFB Operations Enhancements Design

## Scope

Add operational visibility, guarded recovery, richer delivery policies, media diagnostics, backup visibility, update visibility, and secret scanning to the existing EFB deployment.

## Safety rules

- Never restart EFB merely because WeChat is logged out.
- A stalled delivery may restart only the EFB service once, after 10 minutes, with a one-hour cooldown.
- Repeated stalls alert and stop automatic recovery.
- No feature deletes Telegram messages, WeChat messages, attachments, backups, files, or directories.
- Bulk policy changes require preview and explicit confirmation.
- Telegram output never contains tokens, passwords, API credentials, QR data, or complete environment values.
- All mutable settings and state live below `/data/profiles/comwechat/blueset.telegram/` or `/data/operations/state/`.

## Components

### Telegram operations UI

Add `/health`, `/version`, `/backup_info`, `/filetest`, and `/security` commands. Every response uses an inline Close button. Health reads process uptime, WeChat login state, local Bot API reachability, storage report, backup status, and last observed delivery state.

### Delivery policy enhancements

Extend `/filter` with policy and chat-type filters, counts, bulk preview and confirmation, and persisted quiet hours. Existing per-chat behavior remains the default and no existing policy is reset.

### Delivery monitoring and failure reporting

Record inbound, successful outbound, filtered, and failed delivery events in a small atomic JSON state file. A host-side guard checks this state. It alerts on a pending delivery older than 10 minutes. If WeChat is logged in and the cooldown permits, it recreates only the EFB container once. If WeChat is logged out it only alerts.

Failed media delivery reports include message type, size, and a sanitized reason. A retry button is shown only while the original message and readable file remain available in the running process.

### Host operations

Docker JSON logs rotate at 20 MB with three files. Backup status remains read-only. Secret scanning reports suspicious files and key names without printing values. Image updates retain immutable commit tags for rollback.

## Verification

Unit tests cover formatting, redaction, policy selection, quiet-hour calculation, recovery decisions, cooldown behavior, and retry eligibility. Deployment verification checks the GitHub image build, a fresh configuration backup, container health, command registration, persistent paths, and a logged-out recovery decision that never restarts EFB.
