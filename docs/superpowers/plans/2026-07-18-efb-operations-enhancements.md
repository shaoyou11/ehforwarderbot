# EFB Operations Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe diagnostics, recovery, policy controls, failure reporting, backup/version visibility, log rotation, and secret scanning to the EFB stack.

**Architecture:** Telegram Master owns user-facing commands and delivery telemetry. Host operations scripts own Docker inspection and guarded recovery. JSON files under persistent mounts exchange only non-secret status.

**Tech Stack:** Python 3, python-telegram-bot, EFB, Docker Compose, unittest/pytest, GitHub Actions.

## Global Constraints

- Never restart EFB when WeChat is logged out.
- At most one EFB restart per hour for a confirmed delivery stall.
- Never bulk-delete files or directories.
- Never display credentials.
- Every new Telegram page includes a Close button.
- Bulk policy updates require preview and confirmation.

---

### Task 1: Diagnostics commands

**Files:**
- Create: `efb_telegram_master/operations_ui.py`
- Modify: `efb_telegram_master/__init__.py`
- Test: `tests/unit/test_operations_ui.py`

- [ ] Write failing tests for health, version, backup, Bot API, and security output.
- [ ] Implement read-only collectors and command callbacks with Close buttons.
- [ ] Register `/health`, `/version`, `/backup_info`, `/filetest`, and `/security`.
- [ ] Run targeted tests and commit.

### Task 2: Policy filters and quiet hours

**Files:**
- Modify: `efb_telegram_master/delivery_policy.py`
- Modify: `efb_telegram_master/delivery_policy_ui.py`
- Test: `tests/unit/test_delivery_policy.py`
- Test: `tests/unit/test_delivery_policy_ui.py`

- [ ] Write failing tests for counts, policy/type filters, quiet hours, preview, confirmation, and cancellation.
- [ ] Add persisted quiet-hour and bulk-policy data without changing current defaults.
- [ ] Add list controls and confirmation callbacks.
- [ ] Run targeted tests and commit.

### Task 3: Delivery telemetry and media failure UI

**Files:**
- Create: `efb_telegram_master/delivery_telemetry.py`
- Modify: `efb_telegram_master/slave_message.py`
- Modify: `efb_telegram_master/__init__.py`
- Test: `tests/unit/test_delivery_telemetry.py`
- Test: `tests/unit/test_slave_message.py`

- [ ] Write failing tests for atomic state, redacted failures, and retry eligibility.
- [ ] Record inbound, delivered, filtered, and failed states.
- [ ] Send a concise failure notice and expose retry only while source data remains valid.
- [ ] Run targeted tests and commit.

### Task 4: Guarded host recovery and secret audit

**Files:**
- Create: `operations/delivery_guard.py`
- Create: `operations/security_audit.py`
- Modify: `operations/test_operations.py`
- Modify: `docker-compose.example.yaml`

- [ ] Write failing tests proving logged-out WeChat never triggers restart and cooldown is enforced.
- [ ] Implement state inspection, alerting, and single-service recovery decisions.
- [ ] Implement value-redacting secret scan.
- [ ] Set Docker log rotation to 20 MB and three files.
- [ ] Run operations tests and commit to the private configuration repository.

### Task 5: Image integration and deployment

**Files:**
- Modify: `Dockerfile`

- [ ] Pin the tested Telegram Master commit.
- [ ] Build and publish the image through GitHub Actions.
- [ ] Create a fresh NAS configuration backup.
- [ ] Pull and recreate only EFB and required operations service configuration.
- [ ] Verify health, command registration, persistence, and logged-out no-restart behavior.
- [ ] Commit and push all repositories as `shaoyou11`.
